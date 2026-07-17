#!/usr/bin/env python3
"""安全更新或回滚通过 GitHub Release 安装的 CodeBuddy2API。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


_SCRIPT_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_PROJECT_ROOT))

from release_runtime_lock import (  # noqa: E402
    LOCK_FILENAME,
    RuntimeLockBusy,
    RuntimeLockError,
    acquire_runtime_lock,
)


PROJECT_ROOT_NAME = "codebuddy2api"
MANIFEST_FILENAME = "RELEASE_MANIFEST.json"
RUNTIME_LOCK_MODULE_FILENAME = "release_runtime_lock.py"
BACKUP_DIRECTORY_NAME = ".update-backups"
LATEST_BACKUP_NAME = "latest"
MANIFEST_SCHEMA_VERSION = 1
REPLACE_DIRECTORIES = ("frontend", "scripts", "src")
TAG_PATTERN = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
NORMALIZED_DISTRIBUTION_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MINIMUM_PYTHON_VERSION = (3, 10)
MINIMUM_PIP_VERSION = (23, 0)
MINIMUM_PIP_REQUIREMENT = "pip>=23.0"
PIP_BOOTSTRAP_PACKAGES = frozenset({"pip", "setuptools", "wheel"})
REQUIRED_RELEASE_FILES = frozenset(
    {
        MANIFEST_FILENAME,
        "VERSION",
        "README.md",
        "release_runtime_lock.py",
        "requirements.txt",
        "web.py",
        "frontend/package.json",
        "frontend/dist/index.html",
        "scripts/update_release.py",
    }
)
RELEASE_BASE_URL = "https://github.com/iceean/codebuddy2api/releases"


class UpdateError(RuntimeError):
    """更新输入或更新事务不安全。"""


class UpdateCancelled(RuntimeError):
    """用户主动取消更新或回滚。"""


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    files: tuple[str, ...]
    replace_directories: tuple[str, ...]


@dataclass(frozen=True)
class StagedRelease:
    root: Path
    version: str
    manifest: ReleaseManifest


@dataclass(frozen=True)
class RollbackResult:
    cleanup_warnings: tuple[str, ...]


def resolve_project_root(script_path: Path | None = None) -> Path:
    """始终根据脚本位置定位项目根目录，不依赖当前工作目录。"""
    resolved_script = (script_path or Path(__file__)).resolve()
    if resolved_script.parent.name != "scripts":
        raise UpdateError("更新脚本必须位于项目的 scripts 目录中")
    return resolved_script.parents[1]


def validate_release_filename(path: Path) -> None:
    name = path.name
    if not name.startswith(PROJECT_ROOT_NAME) or not (
        name.endswith(".zip") or name.endswith(".tar.gz")
    ):
        raise UpdateError(
            "Release 文件名必须以 codebuddy2api 开头，并以 .zip 或 .tar.gz 结尾"
        )


def _normalized_relative_path(value: str, *, context: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise UpdateError(f"{context}包含非法路径")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise UpdateError(f"{context}包含非法路径")
    return path.as_posix()


def _archive_relative_path(name: str) -> str | None:
    normalized = _normalized_relative_path(name.rstrip("/"), context="压缩包")
    parts = PurePosixPath(normalized).parts
    if not parts or parts[0] != PROJECT_ROOT_NAME:
        raise UpdateError("压缩包必须只包含 codebuddy2api 顶层目录")
    if len(parts) == 1:
        return None
    return PurePosixPath(*parts[1:]).as_posix()


def _casefold_path(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _record_archive_member(
    members: dict[str, object], folded_paths: set[str], relative_path: str, member: object
) -> None:
    folded = _casefold_path(relative_path)
    if relative_path in members or folded in folded_paths:
        raise UpdateError(f"压缩包包含重复或大小写冲突的路径：{relative_path}")
    members[relative_path] = member
    folded_paths.add(folded)


def _safe_destination(staging_root: Path, relative_path: str) -> Path:
    destination = staging_root / PROJECT_ROOT_NAME / PurePosixPath(relative_path)
    release_root = (staging_root / PROJECT_ROOT_NAME).resolve()
    try:
        destination.resolve().relative_to(release_root)
    except ValueError as error:
        raise UpdateError("压缩包包含越界路径") from error
    return destination


def _stage_zip(archive_path: Path, staging_root: Path) -> set[str]:
    members: dict[str, zipfile.ZipInfo] = {}
    folded_paths: set[str] = set()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                relative_path = _archive_relative_path(member.filename)
                mode = member.external_attr >> 16
                if member.is_dir():
                    if mode and not stat.S_ISDIR(mode):
                        raise UpdateError("ZIP 中只允许普通文件和目录")
                    continue
                if not mode or not stat.S_ISREG(mode):
                    raise UpdateError("ZIP 中只允许普通文件和目录")
                if relative_path is None:
                    raise UpdateError("ZIP 顶层目录不能是普通文件")
                _record_archive_member(members, folded_paths, relative_path, member)

            for relative_path, member in members.items():
                destination = _safe_destination(staging_root, relative_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, zipfile.BadZipFile) as error:
        raise UpdateError(f"无法读取 ZIP Release：{error}") from error
    return set(members)


def _stage_tar(archive_path: Path, staging_root: Path) -> set[str]:
    members: dict[str, tarfile.TarInfo] = {}
    folded_paths: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                relative_path = _archive_relative_path(member.name)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise UpdateError("TAR.GZ 中只允许普通文件和目录")
                if relative_path is None:
                    raise UpdateError("TAR.GZ 顶层目录不能是普通文件")
                _record_archive_member(members, folded_paths, relative_path, member)

            for relative_path, member in members.items():
                source = archive.extractfile(member)
                if source is None:
                    raise UpdateError(f"无法读取 TAR.GZ 成员：{relative_path}")
                destination = _safe_destination(staging_root, relative_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, tarfile.TarError) as error:
        raise UpdateError(f"无法读取 TAR.GZ Release：{error}") from error
    return set(members)


def _load_manifest(release_root: Path, actual_files: set[str] | None = None) -> ReleaseManifest:
    manifest_path = release_root / MANIFEST_FILENAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError("Release 清单缺失或无法解析") from error

    if not isinstance(raw, dict) or raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise UpdateError("Release 清单版本不受支持")
    version = raw.get("version")
    files = raw.get("files")
    replace_directories = raw.get("replace_directories")
    if (
        not isinstance(version, str)
        or TAG_PATTERN.fullmatch(version) is None
        or not isinstance(files, list)
        or not all(isinstance(path, str) for path in files)
        or not isinstance(replace_directories, list)
        or not all(isinstance(path, str) for path in replace_directories)
    ):
        raise UpdateError("Release 清单字段无效")

    normalized_files = tuple(
        _normalized_relative_path(path, context="Release 清单") for path in files
    )
    if list(normalized_files) != sorted(normalized_files) or len(
        {_casefold_path(path) for path in normalized_files}
    ) != len(normalized_files):
        raise UpdateError("Release 清单文件列表必须有序且唯一")
    normalized_directories = tuple(
        _normalized_relative_path(path, context="Release 清单")
        for path in replace_directories
    )
    if normalized_directories != REPLACE_DIRECTORIES:
        raise UpdateError("Release 清单的替换目录不受支持")
    if not REQUIRED_RELEASE_FILES.issubset(normalized_files):
        raise UpdateError("Release 清单缺少必要运行文件")
    if actual_files is not None and set(normalized_files) != actual_files:
        raise UpdateError("Release 清单与压缩包实际文件不一致")
    return ReleaseManifest(version, normalized_files, normalized_directories)


def _read_web_version(web_path: Path) -> str:
    try:
        module = ast.parse(web_path.read_text(encoding="utf-8"), filename=str(web_path))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise UpdateError("无法读取 Release 后端版本") from error
    values: list[ast.expr] = []
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION"
            for target in statement.targets
        ):
            values.append(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "APP_VERSION"
            and statement.value is not None
        ):
            values.append(statement.value)
    if len(values) != 1 or not isinstance(values[0], ast.Constant) or not isinstance(
        values[0].value, str
    ):
        raise UpdateError("Release 后端版本定义无效")
    return values[0].value


def _validate_release_versions(release_root: Path, manifest: ReleaseManifest) -> None:
    try:
        version_file = (release_root / "VERSION").read_text(encoding="utf-8").strip()
        frontend_version = json.loads(
            (release_root / "frontend" / "package.json").read_text(encoding="utf-8")
        ).get("version")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as error:
        raise UpdateError("无法读取 Release 版本信息") from error
    plain_version = manifest.version.removeprefix("v")
    if (
        version_file != manifest.version
        or _read_web_version(release_root / "web.py") != plain_version
        or frontend_version != plain_version
    ):
        raise UpdateError("Release 清单、前后端与 VERSION 的版本不一致")


def stage_local_release(archive_path: Path, staging_root: Path) -> StagedRelease:
    archive_path = archive_path.resolve()
    validate_release_filename(archive_path)
    if not archive_path.is_file():
        raise UpdateError(f"Release 文件不存在或不是普通文件：{archive_path}")
    try:
        staging_root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise UpdateError(f"暂存目录已经存在：{staging_root}") from error
    except OSError as error:
        raise UpdateError(f"无法创建暂存目录：{error}") from error

    if archive_path.name.endswith(".zip"):
        actual_files = _stage_zip(archive_path, staging_root)
    else:
        actual_files = _stage_tar(archive_path, staging_root)
    release_root = staging_root / PROJECT_ROOT_NAME
    manifest = _load_manifest(release_root, actual_files)
    _validate_release_versions(release_root, manifest)
    return StagedRelease(release_root, manifest.version, manifest)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _validate_backup_entry(path: Path) -> None:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or stat.S_ISREG(mode):
        return
    if not stat.S_ISDIR(mode):
        raise UpdateError(f"项目中存在无法备份的特殊文件：{path}")
    with os.scandir(path) as entries:
        for entry in entries:
            _validate_backup_entry(Path(entry.path))


def _copy_entry(source: Path, destination: Path) -> None:
    mode = source.lstat().st_mode
    if stat.S_ISLNK(mode):
        os.symlink(
            os.readlink(source),
            destination,
            target_is_directory=source.is_dir(),
        )
    elif stat.S_ISDIR(mode):
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    elif stat.S_ISREG(mode):
        shutil.copy2(source, destination)
    else:
        raise UpdateError(f"项目中存在无法备份的特殊文件：{source}")


def _backup_root(project_root: Path) -> Path:
    backup_root = project_root / BACKUP_DIRECTORY_NAME
    if backup_root.is_symlink():
        raise UpdateError("备份目录不能是符号链接")
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        backup_root.chmod(0o700)
    except OSError as error:
        raise UpdateError(f"无法保护备份目录权限：{error}") from error
    return backup_root


def _create_pending_backup(
    project_root: Path, source_version: str, target_version: str, operation: str
) -> Path:
    backup_root = _backup_root(project_root)
    pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=backup_root))
    snapshot = pending / "project"
    snapshot.mkdir()
    try:
        for entry in sorted(project_root.iterdir(), key=lambda path: path.name):
            if entry.name in (BACKUP_DIRECTORY_NAME, LOCK_FILENAME):
                continue
            _validate_backup_entry(entry)
            _copy_entry(entry, snapshot / entry.name)
        metadata = {
            "operation": operation,
            "source_version": source_version,
            "target_version": target_version,
        }
        (pending / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    return pending


def _promote_latest_backup(backup_root: Path, pending: Path) -> Path:
    latest = backup_root / LATEST_BACKUP_NAME
    previous = backup_root / ".previous"
    _remove_path(previous)
    if latest.exists():
        os.replace(latest, previous)
    try:
        os.replace(pending, latest)
    except Exception:
        if previous.exists():
            os.replace(previous, latest)
        raise
    _remove_other_backups(backup_root, latest)
    return latest


def _remove_other_backups(backup_root: Path, *retained: Path) -> None:
    retained_paths = set(retained)
    for entry in tuple(backup_root.iterdir()):
        if entry not in retained_paths:
            _remove_path(entry)


def _remove_other_backups_after_commit(
    backup_root: Path,
    retained: Path,
) -> tuple[str, ...]:
    try:
        entries = tuple(backup_root.iterdir())
    except OSError as error:
        return (f"无法扫描备份目录 {backup_root}：{error}",)

    cleanup_warnings = []
    for entry in entries:
        if entry == retained:
            continue
        try:
            _remove_path(entry)
        except OSError as error:
            cleanup_warnings.append(f"无法删除遗留备份 {entry}：{error}")
    return tuple(cleanup_warnings)


def create_latest_backup(
    project_root: Path, source_version: str, target_version: str
) -> Path:
    try:
        pending = _create_pending_backup(
            project_root,
            source_version,
            target_version,
            "update",
        )
    except UpdateError:
        raise
    except OSError as error:
        raise UpdateError(f"创建完整备份失败：{error}") from error
    try:
        return _promote_latest_backup(_backup_root(project_root), pending)
    except Exception as error:
        _remove_path(pending)
        raise UpdateError(f"无法替换最近备份：{error}") from error


def _restore_snapshot(project_root: Path, snapshot: Path) -> None:
    if not snapshot.is_dir():
        raise UpdateError("完整备份缺少项目快照")
    for entry in tuple(project_root.iterdir()):
        if entry.name not in (
            BACKUP_DIRECTORY_NAME,
            LOCK_FILENAME,
            RUNTIME_LOCK_MODULE_FILENAME,
        ):
            _remove_path(entry)
    for entry in sorted(snapshot.iterdir(), key=lambda path: path.name):
        _validate_backup_entry(entry)
        destination = project_root / entry.name
        if entry.name == RUNTIME_LOCK_MODULE_FILENAME:
            _atomic_copy_file(entry, destination)
        else:
            _copy_entry(entry, destination)


def rollback_latest(project_root: Path) -> RollbackResult:
    try:
        backup_root = _backup_root(project_root)
    except UpdateError:
        raise
    except OSError as error:
        raise UpdateError(f"无法读取最近完整备份：{error}") from error
    latest = backup_root / LATEST_BACKUP_NAME
    selected_snapshot = latest / "project"
    if not selected_snapshot.is_dir():
        raise UpdateError("没有可用的最近完整备份")
    try:
        current_version = _read_installed_version(project_root)
    except UpdateError:
        current_version = "unknown"
    metadata = _read_backup_metadata(latest)
    try:
        pending = _create_pending_backup(
            project_root,
            current_version,
            str(metadata.get("source_version", "unknown")),
            "rollback",
        )
    except UpdateError:
        raise
    except OSError as error:
        raise UpdateError(f"创建回滚前安全备份失败：{error}") from error
    try:
        _remove_other_backups(backup_root, latest, pending)
    except OSError as error:
        try:
            _remove_path(pending)
        except OSError as pending_error:
            raise UpdateError(
                "清理陈旧备份失败，回滚尚未开始；"
                f"回滚前安全备份已保留在 {pending}：{pending_error}"
            ) from pending_error
        raise UpdateError(f"清理陈旧备份失败，回滚尚未开始：{error}") from error
    try:
        _restore_snapshot(project_root, selected_snapshot)
    except Exception as error:
        try:
            _restore_snapshot(project_root, pending / "project")
        except Exception as restore_error:
            raise UpdateError(
                "回滚失败且恢复回滚前状态失败："
                f"{error}；恢复错误：{restore_error}；"
                f"安全备份已保留在 {pending}"
            ) from restore_error
        _remove_path(pending)
        raise UpdateError(f"回滚失败，已恢复回滚前状态：{error}") from error

    rollback_source = backup_root / ".rollback-source"
    try:
        os.replace(latest, rollback_source)
        os.replace(pending, latest)
    except Exception as error:
        if rollback_source.exists() and not latest.exists():
            os.replace(rollback_source, latest)
        _restore_snapshot(project_root, pending / "project")
        _remove_path(pending)
        raise UpdateError(
            f"无法轮换回滚备份，已恢复回滚前状态：{error}"
        ) from error
    return RollbackResult(
        cleanup_warnings=_remove_other_backups_after_commit(backup_root, latest)
    )


def _read_backup_metadata(backup: Path) -> dict[str, object]:
    try:
        value = json.loads((backup / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError("最近完整备份的元数据无效") from error
    if not isinstance(value, dict):
        raise UpdateError("最近完整备份的元数据无效")
    return value


def _read_installed_version(project_root: Path) -> str:
    try:
        version = (project_root / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise UpdateError("当前目录不是包含 VERSION 的 Release 安装目录") from error
    if TAG_PATTERN.fullmatch(version) is None:
        raise UpdateError("当前 Release 版本无效")
    return version


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = TAG_PATTERN.fullmatch(version)
    if match is None:
        raise UpdateError(f"版本必须是稳定语义版本：{version}")
    return tuple(int(part) for part in match.groups())


def _is_under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(f"{directory}/")


def _atomic_copy_file(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        shutil.copy2(source, temporary_path)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _validate_deployment_destination(
    project_root: Path,
    relative_path: str,
    *,
    destination_is_directory: bool,
    removable_files: frozenset[str],
) -> None:
    current = project_root
    parts = PurePosixPath(relative_path).parts
    for index, part in enumerate(parts):
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise UpdateError(
                f"无法检查 Release 部署路径：{relative_path}"
            ) from error
        if stat.S_ISLNK(mode):
            raise UpdateError(f"Release 部署路径不能包含符号链接：{relative_path}")
        is_destination = index == len(parts) - 1
        if not is_destination and not stat.S_ISDIR(mode):
            current_relative_path = PurePosixPath(*parts[: index + 1]).as_posix()
            if stat.S_ISREG(mode) and current_relative_path in removable_files:
                return
            raise UpdateError(f"Release 部署路径类型无效：{relative_path}")
        if is_destination:
            expected_type = (
                stat.S_ISDIR if destination_is_directory else stat.S_ISREG
            )
            if not expected_type(mode):
                raise UpdateError(f"Release 部署路径类型无效：{relative_path}")


def _venv_python_path(venv_path: Path) -> Path:
    return (
        venv_path / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv_path / "bin" / "python3"
    )


def _run_venv_command(
    command: list[str],
    *,
    context: str,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=capture_output,
            text=capture_output,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise UpdateError(f"{context}失败：{error}") from error


def _normalize_distribution_name(value: object, *, context: str) -> str:
    if not isinstance(value, str):
        raise UpdateError(f"{context}包含无效的软件包名称")
    normalized = re.sub(r"[-_.]+", "-", value).lower()
    if NORMALIZED_DISTRIBUTION_PATTERN.fullmatch(normalized) is None:
        raise UpdateError(f"{context}包含无效的软件包名称")
    return normalized


def _load_json_value(value: str, *, context: str) -> object:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise UpdateError(f"{context}不是有效 JSON") from error


def _venv_uses_system_site_packages(configuration: Path) -> bool:
    try:
        lines = configuration.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise UpdateError("无法读取旧虚拟环境的 pyvenv.cfg") from error

    configured_values = []
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip().lower() == "include-system-site-packages":
            configured_values.append(value.strip().lower())
    if len(configured_values) != 1 or configured_values[0] not in ("true", "false"):
        raise UpdateError(
            "旧虚拟环境的 include-system-site-packages 配置必须唯一且为 true 或 false"
        )
    return configured_values[0] == "true"


def _inspect_reusable_venv(project_root: Path) -> tuple[Path, Path, str]:
    venv_path = project_root / "venv"
    configuration = venv_path / "pyvenv.cfg"
    if venv_path.is_symlink() or not venv_path.is_dir():
        raise UpdateError("无法复用旧虚拟环境：venv 不存在或不是安全目录")
    if configuration.is_symlink() or not configuration.is_file():
        raise UpdateError("无法复用旧虚拟环境：pyvenv.cfg 缺失或不安全")
    if _venv_uses_system_site_packages(configuration):
        raise UpdateError(
            "旧虚拟环境启用了系统 site-packages，无法安全复用；"
            "请移除 --reuse-venv，使用默认的虚拟环境重建模式"
        )
    venv_python = _venv_python_path(venv_path)
    if not venv_python.exists() or not venv_python.resolve().is_file():
        raise UpdateError("无法复用旧虚拟环境：Python 解释器不存在")

    inspection_code = (
        "import importlib.metadata,json,sys;"
        "print(json.dumps({'prefix':sys.prefix,"
        "'version':list(sys.version_info[:2]),"
        "'pip_version':importlib.metadata.version('pip')}))"
    )
    result = _run_venv_command(
        [str(venv_python), "-c", inspection_code],
        context="检查旧虚拟环境",
        capture_output=True,
    )
    inspection = _load_json_value(result.stdout, context="旧虚拟环境检查结果")
    if not isinstance(inspection, dict):
        raise UpdateError("旧虚拟环境检查结果格式无效")
    prefix = inspection.get("prefix")
    version = inspection.get("version")
    pip_version = inspection.get("pip_version")
    if (
        not isinstance(prefix, str)
        or Path(prefix).resolve() != venv_path.resolve()
        or not isinstance(version, list)
        or len(version) != 2
        or any(type(part) is not int for part in version)
        or tuple(version) < MINIMUM_PYTHON_VERSION
        or not isinstance(pip_version, str)
        or not pip_version
    ):
        raise UpdateError("旧虚拟环境与当前 Release 不兼容")
    return venv_path, venv_python, pip_version


def _supported_pip_version(value: str) -> bool:
    match = re.fullmatch(
        r"([0-9]+)\.([0-9]+)(?:\.[0-9]+)*"
        r"(?:\.post[0-9]+)?(?:\+[a-z0-9]+(?:[._-][a-z0-9]+)*)?",
        value.lower(),
    )
    if match is None:
        return False
    return tuple(int(part) for part in match.groups()) >= MINIMUM_PIP_VERSION


def _ensure_supported_pip(
    project_root: Path,
    venv_python: Path,
    current_version: str,
) -> None:
    if _supported_pip_version(current_version):
        return
    _run_venv_command(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            MINIMUM_PIP_REQUIREMENT,
        ],
        context="升级旧虚拟环境 pip",
    )
    _venv_path, inspected_python, upgraded_version = _inspect_reusable_venv(
        project_root
    )
    if inspected_python != venv_python or not _supported_pip_version(upgraded_version):
        raise UpdateError("旧虚拟环境 pip 升级后仍低于 23.0")


def _desired_distribution_names(report_path: Path) -> frozenset[str]:
    try:
        report_value = report_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise UpdateError("无法读取 pip 依赖解析报告") from error
    report = _load_json_value(report_value, context="pip 依赖解析报告")
    if not isinstance(report, dict) or report.get("version") != "1":
        raise UpdateError("pip 依赖解析报告版本不受支持")
    if not isinstance(report.get("install"), list):
        raise UpdateError("pip 依赖解析报告格式无效")

    desired = set(PIP_BOOTSTRAP_PACKAGES)
    for item in report["install"]:
        if not isinstance(item, dict) or not isinstance(item.get("metadata"), dict):
            raise UpdateError("pip 依赖解析报告格式无效")
        desired.add(
            _normalize_distribution_name(
                item["metadata"].get("name"),
                context="pip 依赖解析报告",
            )
        )
    if desired == PIP_BOOTSTRAP_PACKAGES:
        raise UpdateError("pip 依赖解析报告没有包含应用依赖")
    return frozenset(desired)


def _installed_distribution_names(value: str) -> frozenset[str]:
    installed_value = _load_json_value(value, context="pip 已安装软件包列表")
    if not isinstance(installed_value, list):
        raise UpdateError("pip 已安装软件包列表格式无效")
    installed = set()
    for item in installed_value:
        if not isinstance(item, dict):
            raise UpdateError("pip 已安装软件包列表格式无效")
        installed.add(
            _normalize_distribution_name(
                item.get("name"),
                context="pip 已安装软件包列表",
            )
        )
    return frozenset(installed)


def _synchronize_virtual_environment(project_root: Path) -> tuple[str, ...]:
    _venv_path, venv_python, pip_version = _inspect_reusable_venv(project_root)
    _ensure_supported_pip(project_root, venv_python, pip_version)
    requirements = project_root / "requirements.txt"
    with tempfile.TemporaryDirectory(prefix="codebuddy2api-pip-report-") as temp_dir:
        report_path = Path(temp_dir) / "report.json"
        _run_venv_command(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--ignore-installed",
                "--quiet",
                "--report",
                str(report_path),
                "-r",
                str(requirements),
            ],
            context="解析新版 Python 依赖",
        )
        desired = _desired_distribution_names(report_path)

    _run_venv_command(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--upgrade-strategy",
            "eager",
            "-r",
            str(requirements),
        ],
        context="增量安装新版 Python 依赖",
    )
    installed_result = _run_venv_command(
        [str(venv_python), "-m", "pip", "list", "--format=json"],
        context="读取已安装 Python 软件包",
        capture_output=True,
    )
    installed = _installed_distribution_names(installed_result.stdout)
    extras = tuple(sorted(installed - desired))
    if extras:
        _run_venv_command(
            [str(venv_python), "-m", "pip", "uninstall", "-y", *extras],
            context="清理旧 Python 依赖",
        )
    _run_venv_command(
        [str(venv_python), "-m", "pip", "check"],
        context="验证 Python 依赖",
    )
    return extras


def _rebuild_virtual_environment(project_root: Path) -> None:
    venv_path = project_root / "venv"
    _remove_path(venv_path)
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        subprocess.run(
            [
                str(_venv_python_path(venv_path)),
                "-m",
                "pip",
                "install",
                "-r",
                str(project_root / "requirements.txt"),
            ],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise UpdateError(f"重建虚拟环境失败：{error}") from error


def _deploy_release(
    project_root: Path,
    current_manifest: ReleaseManifest,
    staged: StagedRelease,
    *,
    reuse_venv: bool = False,
) -> None:
    managed_directories = set(staged.manifest.replace_directories)
    removable_files = frozenset(
        relative_path
        for relative_path in current_manifest.files
        if relative_path != RUNTIME_LOCK_MODULE_FILENAME
        and not any(
            _is_under(relative_path, directory) for directory in managed_directories
        )
    )
    for directory in staged.manifest.replace_directories:
        _validate_deployment_destination(
            project_root,
            directory,
            destination_is_directory=True,
            removable_files=removable_files,
        )
    for relative_path in staged.manifest.files:
        if any(_is_under(relative_path, directory) for directory in managed_directories):
            continue
        _validate_deployment_destination(
            project_root,
            relative_path,
            destination_is_directory=False,
            removable_files=removable_files,
        )

    for directory in staged.manifest.replace_directories:
        destination = project_root / directory
        _remove_path(destination)
        shutil.copytree(staged.root / directory, destination, copy_function=shutil.copy2)

    for relative_path in current_manifest.files:
        if any(_is_under(relative_path, directory) for directory in managed_directories):
            continue
        if relative_path == RUNTIME_LOCK_MODULE_FILENAME:
            continue
        _remove_path(project_root / PurePosixPath(relative_path))
    for relative_path in staged.manifest.files:
        if any(_is_under(relative_path, directory) for directory in managed_directories):
            continue
        source = staged.root / PurePosixPath(relative_path)
        destination = project_root / PurePosixPath(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative_path == RUNTIME_LOCK_MODULE_FILENAME:
            _atomic_copy_file(source, destination)
        else:
            shutil.copy2(source, destination)

    if reuse_venv:
        _synchronize_virtual_environment(project_root)
    else:
        _rebuild_virtual_environment(project_root)


def _ensure_execution_context(project_root: Path) -> None:
    if (project_root / ".git").exists():
        raise UpdateError("更新器只能用于 Release 安装目录，不能覆盖 Git 工作区")
    try:
        Path(sys.executable).resolve().relative_to(project_root.resolve())
    except ValueError:
        pass
    else:
        raise UpdateError(
            "请退出项目虚拟环境，并使用项目目录外的系统 Python 执行更新器"
        )


def _ensure_safe_installation(project_root: Path) -> ReleaseManifest:
    _ensure_execution_context(project_root)
    manifest = _load_manifest(project_root)
    for relative_path in manifest.files:
        current = project_root
        parts = PurePosixPath(relative_path).parts
        for index, part in enumerate(parts):
            current /= part
            try:
                mode = current.lstat().st_mode
            except OSError as error:
                raise UpdateError(
                    "当前 Release 安装目录缺少清单声明的文件"
                ) from error
            if stat.S_ISLNK(mode):
                raise UpdateError(f"Release 管理路径不能包含符号链接：{relative_path}")
            expected_type = stat.S_ISREG if index == len(parts) - 1 else stat.S_ISDIR
            if not expected_type(mode):
                raise UpdateError(f"Release 管理路径类型无效：{relative_path}")
    _validate_release_versions(project_root, manifest)
    return manifest


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "codebuddy2api-updater"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output)
    except (OSError, urllib.error.URLError) as error:
        raise UpdateError(f"下载 Release 失败：{error}") from error


def _verify_remote_checksum(archive_path: Path, checksums_path: Path) -> None:
    expected = None
    try:
        lines = checksums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise UpdateError("无法读取远程 Release 校验和") from error
    for line in lines:
        digest, separator, name = line.partition("  ")
        if separator and name == archive_path.name and re.fullmatch(r"[0-9a-f]{64}", digest):
            expected = digest
            break
    if expected is None:
        raise UpdateError("远程 Release 校验和未包含目标文件")
    actual = hashlib.sha256()
    with archive_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            actual.update(chunk)
    if actual.hexdigest() != expected:
        raise UpdateError("远程 Release 的 SHA-256 校验失败")


def _download_release(tag: str | None, destination: Path) -> Path:
    archive_path = destination / "codebuddy2api.zip"
    checksums_path = destination / "SHA256SUMS.txt"
    if tag is None:
        base_url = f"{RELEASE_BASE_URL}/latest/download"
    else:
        _version_tuple(tag)
        base_url = f"{RELEASE_BASE_URL}/download/{urllib.parse.quote(tag, safe='')}"
    _download(f"{base_url}/{archive_path.name}", archive_path)
    _download(f"{base_url}/{checksums_path.name}", checksums_path)
    _verify_remote_checksum(archive_path, checksums_path)
    return archive_path


def update_project(
    project_root: Path,
    *,
    tag: str | None = None,
    release_file: Path | None = None,
    reuse_venv: bool = False,
) -> tuple[str, str, Path]:
    current_manifest = _ensure_safe_installation(project_root)
    current_version = current_manifest.version
    with tempfile.TemporaryDirectory(prefix="codebuddy2api-update-") as temp_dir:
        temporary_root = Path(temp_dir)
        archive_path = (
            release_file.resolve()
            if release_file is not None
            else _download_release(tag, temporary_root)
        )
        staged = stage_local_release(archive_path, temporary_root / "staging")
        if tag is not None and staged.version != tag:
            raise UpdateError("下载的 Release 版本与指定版本不一致")
        if _version_tuple(staged.version) <= _version_tuple(current_version):
            raise UpdateError("目标 Release 必须严格高于当前版本")

        backup = create_latest_backup(project_root, current_version, staged.version)
        try:
            _deploy_release(
                project_root,
                current_manifest,
                staged,
                reuse_venv=reuse_venv,
            )
        except Exception as error:
            try:
                _restore_snapshot(project_root, backup / "project")
            except Exception as restore_error:
                raise UpdateError(
                    f"更新失败且自动恢复失败：{error}；恢复错误：{restore_error}"
                ) from restore_error
            if isinstance(error, UpdateError):
                raise
            raise UpdateError(f"更新失败，已自动恢复：{error}") from error
    return current_version, staged.version, backup


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    update_parser = subparsers.add_parser("update", help="更新到较新的 Release")
    update_parser.add_argument("-y", "--yes", action="store_true")
    update_parser.add_argument(
        "--reuse-venv",
        action="store_true",
        help="复用旧虚拟环境并清理新版不再需要的依赖",
    )
    source = update_parser.add_mutually_exclusive_group()
    source.add_argument("--tag", help="指定稳定 Release 标签，例如 v1.2.3")
    source.add_argument("--release-file", type=Path, help="指定本地 ZIP 或 TAR.GZ")
    rollback_parser = subparsers.add_parser("rollback", help="恢复最近完整备份")
    rollback_parser.add_argument("-y", "--yes", action="store_true")
    return parser


def _read_interactive_answer(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError as error:
        raise UpdateError("无法从交互式终端读取确认") from error


def _confirm_operation(action: str) -> None:
    while True:
        answer = _read_interactive_answer(
            f"已确认当前 Release 未运行，是否开始{action}？[y/N] "
        )
        if answer in ("y", "yes"):
            return
        if answer in ("", "n", "no", "q"):
            raise UpdateCancelled(f"已取消{action}")
        print("请输入 y 或 n。")


def _run_cli_operation(
    project_root: Path,
    *,
    action: str,
    assume_yes: bool,
    operation,
):
    _ensure_execution_context(project_root)
    interactive = sys.stdin.isatty()
    if not assume_yes and not interactive:
        raise UpdateError("非交互式运行必须传入 --yes")

    while True:
        try:
            runtime_lock = acquire_runtime_lock(
                project_root,
                required=True,
                purpose=action,
            )
            break
        except RuntimeLockBusy as error:
            if assume_yes or not interactive:
                raise UpdateError(
                    "检测到 Release 服务仍在运行，或另一更新/回滚正在进行"
                ) from error
            while True:
                answer = _read_interactive_answer(
                    "检测到服务仍在运行，或另一更新/回滚正在进行。"
                    "请停止相关进程后按 Enter 重新检测，输入 q 取消："
                )
                if answer == "":
                    break
                if answer == "q":
                    raise UpdateCancelled(f"已取消{action}") from error
                print("请按 Enter 重新检测，或输入 q 取消。")
        except RuntimeLockError as error:
            raise UpdateError(str(error)) from error

    if runtime_lock is None:
        raise UpdateError("当前目录没有可用的 Release 运行锁")
    with runtime_lock:
        if not assume_yes:
            _confirm_operation(action)
        return operation()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if not args.yes and not sys.stdin.isatty():
        parser.error("非交互式运行必须传入 --yes")
    project_root = resolve_project_root()
    try:
        if args.command == "update":
            old_version, new_version, backup = _run_cli_operation(
                project_root,
                action="更新",
                assume_yes=args.yes,
                operation=lambda: update_project(
                    project_root,
                    tag=args.tag,
                    release_file=args.release_file,
                    reuse_venv=args.reuse_venv,
                ),
            )
            print(f"更新完成：{old_version} -> {new_version}")
            print(f"最近完整备份：{backup}")
        else:
            rollback_result = _run_cli_operation(
                project_root,
                action="回滚",
                assume_yes=args.yes,
                operation=lambda: rollback_latest(project_root),
            )
            print("回滚完成；回滚前状态已保存为新的最近完整备份。")
            for warning in rollback_result.cleanup_warnings:
                print(
                    f"警告：回滚已经成功，但{warning}。"
                    "请手动清理该路径，不要重新执行回滚。",
                    file=sys.stderr,
                )
    except UpdateCancelled as error:
        print(error)
    except KeyboardInterrupt:
        parser.exit(130, "\n操作已取消。\n")
    except UpdateError as error:
        parser.exit(1, f"更新器错误：{error}\n")


if __name__ == "__main__":
    main()
