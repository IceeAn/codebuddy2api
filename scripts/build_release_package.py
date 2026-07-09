#!/usr/bin/env python3
"""构建可直接本地运行的 Release 包。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import tarfile
import tempfile
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
    "frontend/admin.html",
    "secrets/users.txt.example",
)

REQUIRED_DIRS = (
    "src",
    "scripts",
    "frontend/dist",
    "frontend/public",
)


@dataclass(frozen=True)
class ReleaseArtifacts:
    tarball: Path
    zipfile: Path
    checksums: Path


def _require_file(repository_root: Path, relative_path: str) -> Path:
    source = repository_root / relative_path
    if not source.is_file():
        raise RuntimeError(f"Missing required file: {relative_path}")
    return source


def _require_dir(repository_root: Path, relative_path: str) -> Path:
    source = repository_root / relative_path
    if not source.is_dir():
        raise RuntimeError(f"Missing required directory: {relative_path}")
    return source


def _copy_file(repository_root: Path, package_root: Path, relative_path: str) -> None:
    source = _require_file(repository_root, relative_path)
    destination = package_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_dir(repository_root: Path, package_root: Path, relative_path: str) -> None:
    source = _require_dir(repository_root, relative_path)
    destination = package_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


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

    if not (repository_root / "frontend" / "dist" / "index.html").is_file():
        raise RuntimeError("Missing frontend/dist/index.html. Build the frontend first.")

    package_root = staging_root / PACKAGE_ROOT_NAME
    package_root.mkdir(parents=True)
    (package_root / "VERSION").write_text(f"{tag}\n", encoding="utf-8")

    for relative_path in REQUIRED_FILES:
        _copy_file(repository_root, package_root, relative_path)

    for relative_path in REQUIRED_DIRS:
        _copy_dir(repository_root, package_root, relative_path)

    return package_root


def _archive_members(package_root: Path) -> list[Path]:
    return sorted(path for path in package_root.rglob("*") if path.is_file())


def _write_tarball(package_root: Path, destination: Path) -> None:
    with tarfile.open(destination, "w:gz") as archive:
        for source in _archive_members(package_root):
            archive.add(source, arcname=source.relative_to(package_root.parent))


def _write_zip(package_root: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in _archive_members(package_root):
            archive.write(source, arcname=source.relative_to(package_root.parent))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksums(paths: tuple[Path, ...], destination: Path) -> None:
    lines = [f"{_sha256(path)}  {path.name}\n" for path in paths]
    destination.write_text("".join(lines), encoding="utf-8")


def build_package(
    repository_root: Path,
    tag: str,
    output_dir: Path,
) -> ReleaseArtifacts:
    repository_root = repository_root.resolve()
    output_dir = output_dir.resolve()
    validate_release_version(repository_root, tag)
    output_dir.mkdir(parents=True, exist_ok=True)

    tarball = output_dir / "codebuddy2api.tar.gz"
    zip_path = output_dir / "codebuddy2api.zip"
    checksums = output_dir / "SHA256SUMS.txt"

    with tempfile.TemporaryDirectory() as temp_dir:
        package_root = stage_package(repository_root, tag, Path(temp_dir))
        _write_tarball(package_root, tarball)
        _write_zip(package_root, zip_path)

    _write_checksums((tarball, zip_path), checksums)
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
    args = parser.parse_args()

    if args.validate_only:
        validate_release_version(args.repository_root, args.tag)
        print(f"Validated release version {args.tag}")
        return

    artifacts = build_package(args.repository_root, args.tag, args.output_dir)
    print(f"Wrote {artifacts.tarball}")
    print(f"Wrote {artifacts.zipfile}")
    print(f"Wrote {artifacts.checksums}")


if __name__ == "__main__":
    main()
