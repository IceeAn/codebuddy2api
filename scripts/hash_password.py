#!/usr/bin/env python3
"""已停用的旧用户文件写入命令。"""

import sys


MESSAGE = (
    "hash_password.py 已停用；账号现存储于 SQLite。"
    "请改用 scripts/manage_users.py set-user <用户名>。"
)


def main() -> int:
    """仅给出迁移指引，不解析参数、不读取密码，也不写入任何文件。"""
    print(MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
