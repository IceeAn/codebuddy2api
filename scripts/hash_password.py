#!/usr/bin/env python3
"""
生成 secrets/users.txt 可用的密码哈希。
"""
import argparse
import getpass
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.password_hashing import create_password_hash  # noqa: E402


def _validate_username(username: str) -> None:
    stripped = username.strip()
    if not stripped:
        raise ValueError("用户名不能为空")
    if stripped.startswith("#"):
        raise ValueError("用户名不能以 # 开头")
    if ":" in username:
        raise ValueError("用户名不能包含冒号")
    if "\n" in username or "\r" in username:
        raise ValueError("用户名不能包含换行符")


def _is_username_record(line: str, username: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    existing_username, separator, _password_hash = stripped.partition(":")
    return bool(separator) and existing_username.strip() == username.strip()


def _restricted_mode(source_mode: int) -> int:
    owner_mode = stat.S_IMODE(source_mode) & 0o600
    return owner_mode | 0o400


def _read_users_file(users_file: Path) -> tuple[str, int, int, int]:
    if users_file.is_symlink():
        raise RuntimeError(f"Users file must not be a symbolic link: {users_file}")

    try:
        file_stat = users_file.stat()
    except FileNotFoundError:
        directory_stat = users_file.parent.stat()
        return "", directory_stat.st_uid, directory_stat.st_gid, 0o600

    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"Users file must be a regular file: {users_file}")
    if file_stat.st_nlink != 1:
        raise RuntimeError(f"Users file must not have multiple hard links: {users_file}")

    contents = users_file.read_text(encoding="utf-8")
    return (
        contents,
        file_stat.st_uid,
        file_stat.st_gid,
        _restricted_mode(file_stat.st_mode),
    )


def _replace_file(
    users_file: Path,
    contents: str,
    owner_uid: int,
    owner_gid: int,
    file_mode: int,
) -> None:
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{users_file.name}.",
        dir=users_file.parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
            if os.name == "posix":
                temp_stat = os.fstat(file.fileno())
                if (temp_stat.st_uid, temp_stat.st_gid) != (owner_uid, owner_gid):
                    os.fchown(file.fileno(), owner_uid, owner_gid)
                os.fchmod(file.fileno(), file_mode)
        os.replace(temp_path, users_file)
        if os.name == "posix":
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(users_file.parent, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temp_path.unlink(missing_ok=True)


def replace_user_record(
    users_file: Path,
    username: str,
    password_hash: str,
) -> None:
    """原子写入用户记录；同名用户的旧记录会被删除。"""
    _validate_username(username)
    users_file = Path(users_file)
    users_file.parent.mkdir(parents=True, exist_ok=True)
    contents, owner_uid, owner_gid, file_mode = _read_users_file(users_file)
    remaining_lines = [
        line
        for line in contents.splitlines(keepends=True)
        if not _is_username_record(line, username)
    ]
    updated_contents = "".join(remaining_lines)
    if updated_contents and not updated_contents.endswith(("\n", "\r")):
        updated_contents += "\n"
    updated_contents += f"{username}:{password_hash}\n"
    _replace_file(
        users_file,
        updated_contents,
        owner_uid,
        owner_gid,
        file_mode,
    )


def main():
    parser = argparse.ArgumentParser(description="生成 CodeBuddy2API 用户密码哈希")
    parser.add_argument("username", help="用户名")
    parser.add_argument("--password", help="明文密码；未提供时会交互输入")
    parser.add_argument(
        "--output",
        type=Path,
        help="原子写入用户文件；同名用户将更新密码",
    )
    args = parser.parse_args()

    try:
        _validate_username(args.username)
    except ValueError as error:
        parser.error(str(error))

    password = args.password or getpass.getpass("Password: ")
    password_hash = create_password_hash(password)
    if args.output is None:
        print(f"{args.username}:{password_hash}")
        return
    replace_user_record(args.output, args.username, password_hash)


if __name__ == "__main__":
    main()
