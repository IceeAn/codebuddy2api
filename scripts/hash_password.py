#!/usr/bin/env python3
"""
生成 secrets/users.txt 可用的密码哈希。
"""
import argparse
import getpass
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.password_hashing import create_password_hash  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="生成 CodeBuddy2API 用户密码哈希")
    parser.add_argument("username", help="用户名")
    parser.add_argument("--password", help="明文密码；未提供时会交互输入")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password: ")
    print(f"{args.username}:{create_password_hash(password)}")


if __name__ == "__main__":
    main()
