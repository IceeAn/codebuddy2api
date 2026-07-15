#!/usr/bin/env python3
"""构建可直接本地运行的 Release 包。"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import os
import re
import stat
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path


PACKAGE_ROOT_NAME = "codebuddy2api"
TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")

REQUIRED_FILES = (
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "LICENSING.md",
    ".env.example",
    "docker-compose.yml",
    "requirements.txt",
    "config.py",
    "web.py",
    "secrets/users.txt.example",
)

REQUIRED_DIRS = (
    ("src", frozenset({".py"})),
    ("scripts", frozenset({".py"})),
    ("frontend/dist", None),
    ("frontend/public", None),
)

EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__"})
EXCLUDED_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})
EXCLUDED_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})
ZIP_MIN_TIMESTAMP = 315_532_800
ZIP_MAX_TIMESTAMP = 4_354_819_198
ARCHIVE_FILE_MODE = stat.S_IFREG | 0o644


@dataclass(frozen=True)
class ReleaseArtifacts:
    tarball: Path
    zipfile: Path
    checksums: Path


def _require_file(repository_root: Path, relative_path: str) -> Path:
    source, source_stat = _repository_path_stat(
        repository_root,
        relative_path,
        missing_kind="file",
    )
    if not stat.S_ISREG(source_stat.st_mode):
        raise RuntimeError(f"Required path must be a regular file: {relative_path}")
    return source


def _require_dir(repository_root: Path, relative_path: str) -> Path:
    source, source_stat = _repository_path_stat(
        repository_root,
        relative_path,
        missing_kind="directory",
    )
    if not stat.S_ISDIR(source_stat.st_mode):
        raise RuntimeError(f"Required path must be a directory: {relative_path}")
    return source


def _repository_path_stat(
    repository_root: Path,
    relative_path: str,
    *,
    missing_kind: str,
) -> tuple[Path, os.stat_result]:
    current = repository_root
    for part in Path(relative_path).parts:
        current /= part
        try:
            current_stat = current.lstat()
        except (FileNotFoundError, NotADirectoryError):
            raise RuntimeError(f"Missing required {missing_kind}: {relative_path}")
        if stat.S_ISLNK(current_stat.st_mode):
            path = current.relative_to(repository_root).as_posix()
            raise RuntimeError(f"Symbolic link is not allowed: {path}")
    return current, current_stat


def _read_regular_file(source: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    with os.fdopen(descriptor, "rb") as source_file:
        if not stat.S_ISREG(os.fstat(source_file.fileno()).st_mode):
            raise RuntimeError(f"Release input must be a regular file: {source}")
        return source_file.read()


def _copy_file(repository_root: Path, package_root: Path, relative_path: str) -> None:
    source = _require_file(repository_root, relative_path)
    destination = package_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_read_regular_file(source))


def _directory_files(source: Path) -> list[Path]:
    files: list[Path] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                relative_path = path.relative_to(source)
                if entry.is_symlink():
                    raise RuntimeError(
                        f"Symbolic link is not allowed: {source.name}/{relative_path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in EXCLUDED_DIRECTORY_NAMES:
                        visit(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise RuntimeError(
                        f"Release input must be a regular file: "
                        f"{source.name}/{relative_path}"
                    )
                files.append(path)

    visit(source)
    return files


def _copy_dir(
    repository_root: Path,
    package_root: Path,
    relative_path: str,
    allowed_suffixes: frozenset[str] | None,
) -> None:
    source = _require_dir(repository_root, relative_path)
    destination = package_root / relative_path
    for source_file in _directory_files(source):
        if source_file.name in EXCLUDED_FILE_NAMES:
            continue
        if source_file.suffix in EXCLUDED_FILE_SUFFIXES:
            continue
        if allowed_suffixes is not None and source_file.suffix not in allowed_suffixes:
            continue
        relative_file = source_file.relative_to(source)
        destination_file = destination / relative_file
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        destination_file.write_bytes(_read_regular_file(source_file))


def _read_application_version(repository_root: Path) -> str:
    web_path = _require_file(repository_root, "web.py")
    module = ast.parse(web_path.read_text(encoding="utf-8"), filename=str(web_path))
    assignments: list[ast.expr] = []
    for statement in module.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION"
            for target in statement.targets
        ):
            assignments.append(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "APP_VERSION"
            and statement.value is not None
        ):
            assignments.append(statement.value)

    if len(assignments) != 1:
        raise RuntimeError("web.py must define APP_VERSION exactly once")
    value = assignments[0]
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        raise RuntimeError("web.py APP_VERSION must be a string literal")
    return value.value


def validate_release_version(repository_root: Path, tag: str) -> str:
    repository_root = repository_root.resolve()
    if not TAG_PATTERN.fullmatch(tag):
        raise RuntimeError("Release tag must look like v1.2.3")

    application_version = _read_application_version(repository_root)
    expected_tag = f"v{application_version}"
    if tag != expected_tag:
        raise RuntimeError(
            f"Release tag must be {expected_tag} to match web.py APP_VERSION"
        )

    package_path = _require_file(repository_root, "frontend/package.json")
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if package.get("version") != application_version:
        raise RuntimeError(
            "frontend/package.json version must match web.py APP_VERSION"
        )
    return application_version


def stage_package(repository_root: Path, tag: str, staging_root: Path) -> Path:
    validate_release_version(repository_root, tag)

    try:
        _require_file(repository_root, "frontend/dist/index.html")
    except RuntimeError as error:
        if "Missing required file" not in str(error):
            raise
        raise RuntimeError("Missing frontend/dist/index.html. Build the frontend first.")

    package_root = staging_root / PACKAGE_ROOT_NAME
    package_root.mkdir(parents=True)
    (package_root / "VERSION").write_text(f"{tag}\n", encoding="utf-8")

    for relative_path in REQUIRED_FILES:
        _copy_file(repository_root, package_root, relative_path)

    for relative_path, allowed_suffixes in REQUIRED_DIRS:
        _copy_dir(repository_root, package_root, relative_path, allowed_suffixes)

    return package_root


def _archive_members(package_root: Path) -> list[Path]:
    return sorted(_directory_files(package_root))


def _write_tarball(
    package_root: Path,
    destination: Path,
    source_date_epoch: int,
) -> None:
    with destination.open("wb") as destination_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=destination_file,
            mtime=source_date_epoch,
        ) as compressed_file:
            with tarfile.open(
                fileobj=compressed_file,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                for source in _archive_members(package_root):
                    content = _read_regular_file(source)
                    archive_name = source.relative_to(package_root.parent).as_posix()
                    member = tarfile.TarInfo(archive_name)
                    member.size = len(content)
                    member.mtime = source_date_epoch
                    member.mode = 0o644
                    member.uid = 0
                    member.gid = 0
                    member.uname = ""
                    member.gname = ""
                    archive.addfile(member, io.BytesIO(content))


def _zip_timestamp(source_date_epoch: int) -> tuple[int, int, int, int, int, int]:
    timestamp = max(source_date_epoch, ZIP_MIN_TIMESTAMP)
    if timestamp > ZIP_MAX_TIMESTAMP:
        raise RuntimeError("SOURCE_DATE_EPOCH exceeds the ZIP timestamp range")
    return time.gmtime(timestamp)[:6]


def _write_zip(
    package_root: Path,
    destination: Path,
    source_date_epoch: int,
) -> None:
    zip_timestamp = _zip_timestamp(source_date_epoch)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source in _archive_members(package_root):
            archive_name = source.relative_to(package_root.parent).as_posix()
            member = zipfile.ZipInfo(archive_name, date_time=zip_timestamp)
            member.create_system = 3
            member.external_attr = ARCHIVE_FILE_MODE << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, _read_regular_file(source), compresslevel=9)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksums(paths: tuple[Path, ...], destination: Path) -> None:
    lines = [f"{_sha256(path)}  {path.name}\n" for path in paths]
    destination.write_text("".join(lines), encoding="utf-8")


def _validate_output_directory(repository_root: Path, output_dir: Path) -> None:
    for relative_path, _allowed_suffixes in REQUIRED_DIRS:
        input_dir = (repository_root / relative_path).resolve()
        if output_dir == input_dir or input_dir in output_dir.parents:
            raise RuntimeError(
                "Release output directory must not be inside release input directory: "
                f"{relative_path}"
            )


def build_package(
    repository_root: Path,
    tag: str,
    output_dir: Path,
    *,
    source_date_epoch: int,
) -> ReleaseArtifacts:
    repository_root = repository_root.resolve()
    output_dir = output_dir.resolve()
    validate_release_version(repository_root, tag)
    if source_date_epoch < 0:
        raise RuntimeError("SOURCE_DATE_EPOCH must be a non-negative integer")
    _validate_output_directory(repository_root, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tarball = output_dir / "codebuddy2api.tar.gz"
    zip_path = output_dir / "codebuddy2api.zip"
    checksums = output_dir / "SHA256SUMS.txt"

    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".release-") as temp_dir:
        temporary_root = Path(temp_dir)
        package_root = stage_package(repository_root, tag, temporary_root / "staging")
        temporary_tarball = temporary_root / tarball.name
        temporary_zip = temporary_root / zip_path.name
        temporary_checksums = temporary_root / checksums.name
        _write_tarball(package_root, temporary_tarball, source_date_epoch)
        _write_zip(package_root, temporary_zip, source_date_epoch)
        _write_checksums(
            (temporary_tarball, temporary_zip),
            temporary_checksums,
        )
        os.replace(temporary_tarball, tarball)
        os.replace(temporary_zip, zip_path)
        os.replace(temporary_checksums, checksums)
    return ReleaseArtifacts(tarball=tarball, zipfile=zip_path, checksums=checksums)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Release tag, for example v1.2.3")
    parser.add_argument(
        "--repository-root",
        default=Path(__file__).resolve().parents[1],
        type=Path,
        help="Repository root. Defaults to this script's parent repository.",
    )
    parser.add_argument(
        "--output-dir",
        default=Path("dist") / "release",
        type=Path,
        help="Directory for release archives.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the tag and application versions without building archives.",
    )
    parser.add_argument(
        "--source-date-epoch",
        default=os.environ.get("SOURCE_DATE_EPOCH"),
        type=int,
        help="Normalized archive timestamp. Defaults to SOURCE_DATE_EPOCH.",
    )
    args = parser.parse_args()

    if args.validate_only:
        validate_release_version(args.repository_root, args.tag)
        print(f"Validated release version {args.tag}")
        return

    if args.source_date_epoch is None:
        parser.error("SOURCE_DATE_EPOCH or --source-date-epoch is required")

    artifacts = build_package(
        args.repository_root,
        args.tag,
        args.output_dir,
        source_date_epoch=args.source_date_epoch,
    )
    print(f"Wrote {artifacts.tarball}")
    print(f"Wrote {artifacts.zipfile}")
    print(f"Wrote {artifacts.checksums}")


if __name__ == "__main__":
    main()
