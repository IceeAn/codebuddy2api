"""仅为本地 Release 安装提供跨平台进程级运行锁。"""

from __future__ import annotations

import errno
import json
import os
import re
import stat
from pathlib import Path
from types import TracebackType


LOCK_FILENAME = ".codebuddy2api-runtime.lock"
MANIFEST_FILENAME = "RELEASE_MANIFEST.json"
VERSION_FILENAME = "VERSION"
_VERSION_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
_BUSY_ERRNOS = frozenset(
    value
    for value in (
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", None),
    )
    if value is not None
)


class RuntimeLockError(RuntimeError):
    """运行锁无法安全使用。"""


class RuntimeLockBusy(RuntimeLockError):
    """运行锁已由另一个进程持有。"""


def _is_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _require_regular_file(path: Path, description: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise RuntimeLockError(f"{description}不存在或无法读取：{path}") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise RuntimeLockError(f"{description}必须是非符号链接普通文件：{path}")


def _validate_release_markers(project_root: Path) -> None:
    manifest_path = project_root / MANIFEST_FILENAME
    version_path = project_root / VERSION_FILENAME
    _require_regular_file(manifest_path, "Release 清单")
    _require_regular_file(version_path, "Release 版本文件")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = version_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeLockError("Release 标记无法解析") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("version") != version
        or _VERSION_PATTERN.fullmatch(version) is None
    ):
        raise RuntimeLockError("Release 标记无效或版本不一致")


def is_release_installation(project_root: Path) -> bool:
    """判断服务是否应启用 Release 运行锁。"""
    project_root = project_root.resolve()
    git_path = project_root / ".git"
    if _is_present(git_path):
        return False

    manifest_present = _is_present(project_root / MANIFEST_FILENAME)
    version_present = _is_present(project_root / VERSION_FILENAME)
    if manifest_present or version_present:
        if not manifest_present or not version_present:
            raise RuntimeLockError("Release 标记不完整")
        _validate_release_markers(project_root)
        return True

    lock_path = project_root / LOCK_FILENAME
    if _is_present(lock_path):
        return True
    return False


def _lock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            return
    except OSError as error:
        if error.errno in _BUSY_ERRNOS:
            raise RuntimeLockBusy("运行锁已被其他进程持有") from error
        raise RuntimeLockError(f"运行锁加锁失败：{error}") from error
    raise RuntimeLockError(f"当前平台不支持 Release 运行锁：{os.name}")


def _unlock_descriptor(descriptor: int) -> None:
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
    except OSError as error:
        raise RuntimeLockError(f"运行锁解锁失败：{error}") from error
    raise RuntimeLockError(f"当前平台不支持 Release 运行锁：{os.name}")


def _open_lock_file(lock_path: Path, *, create: bool = True) -> int:
    if lock_path.is_symlink():
        raise RuntimeLockError(f"运行锁必须是非符号链接普通文件：{lock_path}")
    if lock_path.exists() and not lock_path.is_file():
        raise RuntimeLockError(f"运行锁必须是非符号链接普通文件：{lock_path}")

    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
    if create:
        flags |= os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise RuntimeLockError(f"无法打开运行锁：{error}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeLockError(f"运行锁必须是普通文件：{lock_path}")
        os.set_inheritable(descriptor, False)
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


class RuntimeFileLock:
    """持有一个已经成功获取的运行锁。"""

    def __init__(self, path: Path, descriptor: int, purpose: str):
        self.path = path
        self.purpose = purpose
        self._descriptor: int | None = descriptor

    @property
    def acquired(self) -> bool:
        return self._descriptor is not None

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        try:
            _unlock_descriptor(descriptor)
        finally:
            os.close(descriptor)

    def __enter__(self) -> RuntimeFileLock:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()


def acquire_runtime_lock(
    project_root: Path,
    *,
    required: bool,
    purpose: str,
) -> RuntimeFileLock | None:
    """为 Release 安装获取锁，非 Release 服务环境返回 ``None``。"""
    project_root = project_root.resolve()
    lock_path = project_root / LOCK_FILENAME
    create_lock_file = True
    try:
        release_installation = is_release_installation(project_root)
    except RuntimeLockError:
        if not required or not _is_present(lock_path):
            raise
        release_installation = True
        create_lock_file = False

    if not release_installation:
        if required:
            raise RuntimeLockError("当前目录不是可加锁的 Release 安装目录")
        return None

    descriptor = _open_lock_file(lock_path, create=create_lock_file)
    try:
        _lock_descriptor(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    runtime_lock = RuntimeFileLock(lock_path, descriptor, purpose)
    if not required:
        try:
            _validate_release_markers(project_root)
        except Exception:
            runtime_lock.close()
            raise
    return runtime_lock
