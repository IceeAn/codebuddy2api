#!/usr/bin/env python3
"""通过共享 SQLite 数据库管理 CodeBuddy2API 系统账号。"""

import argparse
import getpass
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.users_store import (  # noqa: E402
    AUTH_STATE_UNINITIALIZED,
    SystemUserConfigurationError,
    escape_terminal_text,
    normalize_username,
    users_store,
)


def _add_set_user_parser(subparsers, name: str, help_text: str) -> None:
    command = subparsers.add_parser(name, help=help_text)
    command.add_argument("username", help="用户名")
    command.add_argument("--password", help="明文密码；省略时仅在 TTY 中隐藏输入两次")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理 CodeBuddy2API 系统账号")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_set_user_parser(subparsers, "set-user", "新增账号或重置密码")
    _add_set_user_parser(subparsers, "add-user", "set-user 的完全等价别名")
    listing = subparsers.add_parser("list-users", help="列出账号")
    listing.add_argument("--json", action="store_true", help="输出稳定 JSON")
    deletion = subparsers.add_parser("delete-user", help="删除账号认证记录")
    deletion.add_argument("username", help="用户名")
    deletion.add_argument("--yes", action="store_true", help="跳过交互确认")
    return parser


def _read_password(option_value: Optional[str]) -> str:
    if option_value is not None:
        return option_value
    if not sys.stdin.isatty():
        raise SystemUserConfigurationError(
            "标准输入不是 TTY；请显式提供 --password"
        )
    first = getpass.getpass("新密码：")
    second = getpass.getpass("再次输入新密码：")
    if first != second:
        raise SystemUserConfigurationError("两次输入的密码不一致")
    return first


def _require_initialized() -> None:
    try:
        state = users_store.state()
    except FileNotFoundError as error:
        raise SystemUserConfigurationError(
            "账号系统尚未初始化；请先启动服务或执行 set-user"
        ) from error
    if state == AUTH_STATE_UNINITIALIZED:
        raise SystemUserConfigurationError(
            "账号系统尚未初始化；请先启动服务或执行 set-user"
        )


def _set_user(args) -> None:
    password = _read_password(args.password)
    record = users_store.set_user(args.username, password)
    print(f"账号已设置：{escape_terminal_text(record.username)}")


def _list_users(args) -> None:
    _require_initialized()
    records = users_store.list_records()
    if args.json:
        print(json.dumps(
            {
                "users": [
                    {
                        "username": record.username,
                        "password_change_required": record.password_change_required,
                        "updated_at": record.updated_at,
                    }
                    for record in records
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ))
        return

    print("用户名	强制修改密码	更新时间")
    for record in records:
        updated_at = datetime.fromtimestamp(record.updated_at).astimezone().isoformat(
            timespec="seconds"
        )
        forced = "是" if record.password_change_required else "否"
        print(
            f"{escape_terminal_text(record.username)}	{forced}	{updated_at}"
        )


def _delete_user(args) -> None:
    _require_initialized()
    username = normalize_username(args.username)
    if not args.yes:
        confirmation = input(
            f"请输入完整用户名 {escape_terminal_text(username)} 以确认删除："
        )
        if confirmation != username:
            raise SystemUserConfigurationError("确认不匹配，已取消删除")
    if not users_store.delete_user(username):
        raise SystemUserConfigurationError("账号不存在")
    print(f"账号认证记录已删除：{escape_terminal_text(username)}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"set-user", "add-user"}:
            _set_user(args)
        elif args.command == "list-users":
            _list_users(args)
        else:
            _delete_user(args)
    except (SystemUserConfigurationError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    except sqlite3.Error as error:
        print(f"数据库操作失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
