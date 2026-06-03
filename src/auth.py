"""
Authentication module for CodeBuddy2API
"""
import base64
import binascii
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from fastapi import HTTPException, Request, status

from config import get_users_file_path
from .password_hashing import verify_password

logger = logging.getLogger(__name__)

# 用于不存在用户名时的固定耗时校验，避免通过响应时间枚举有效用户名。
_DUMMY_PASSWORD_HASH = "pbkdf2_sha256$390000$Q2ZpaYeWHUv958nZM_Zl6A$Z7iHCysOlsWDVbFAIt2uxSfhQTD5qYNehS1W65K4DHY"


@dataclass(frozen=True)
class AuthenticatedUser:
    """当前通过认证的用户。"""
    username: str
    source: str


class UsersFileStore:
    """从 secrets/users.txt 加载用户名和密码哈希。"""

    def __init__(self):
        self._users: Dict[str, str] = {}
        self._loaded_path: Optional[Path] = None
        self._loaded_mtime: Optional[float] = None

    def _resolve_users_file(self) -> Path:
        users_file = Path(get_users_file_path())
        if not users_file.is_absolute():
            users_file = Path.cwd() / users_file
        return users_file

    def _load_if_needed(self):
        users_file = self._resolve_users_file()
        try:
            stat_result = users_file.stat()
        except FileNotFoundError:
            self._users = {}
            self._loaded_path = users_file
            self._loaded_mtime = None
            return

        if not users_file.is_file():
            self._users = {}
            self._loaded_path = users_file
            self._loaded_mtime = None
            logger.warning("Configured users file is not a regular file: %s", users_file)
            return

        current_mtime = stat_result.st_mtime

        if self._loaded_path == users_file and self._loaded_mtime == current_mtime:
            return

        users: Dict[str, str] = {}
        with users_file.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                username, separator, password_hash = stripped.partition(":")
                if not separator or not username.strip() or not password_hash.strip():
                    logger.warning("Ignoring invalid users file line %s in %s", line_number, users_file)
                    continue

                users[username.strip()] = password_hash.strip()

        self._users = users
        self._loaded_path = users_file
        self._loaded_mtime = current_mtime
        logger.info("Loaded %s user(s) from %s", len(users), users_file)

    def verify(self, username: str, password: str) -> bool:
        self._load_if_needed()
        password_hash = self._users.get(username)
        if not password_hash:
            verify_password(password, _DUMMY_PASSWORD_HASH)
            return False
        return verify_password(password, password_hash)

    def has_users_file(self) -> bool:
        self._load_if_needed()
        return bool(self._users)


def _parse_basic_credentials(auth_value: str) -> Optional[Tuple[str, str]]:
    try:
        encoded = auth_value.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, separator, password = decoded.partition(":")
        if not separator:
            return None
        return username, password
    except (IndexError, UnicodeDecodeError, binascii.Error):
        return None


def _parse_bearer_credentials(auth_value: str) -> Tuple[Optional[str], str]:
    token = auth_value.split(" ", 1)[1].strip()
    username, separator, password = token.partition(":")
    if separator:
        return username, password
    return None, token


_users_store = UsersFileStore()


def authenticate(request: Request) -> AuthenticatedUser:
    """验证用户身份，支持 Basic 和 Bearer username:password。"""
    auth_value = request.headers.get("Authorization", "")
    scheme = auth_value.split(" ", 1)[0].lower() if auth_value else ""

    if not _users_store.has_users_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No authentication users configured. Mount secrets/users.txt.",
        )

    if scheme == "basic":
        parsed = _parse_basic_credentials(auth_value)
        if not parsed:
            raise _auth_error()
        username, password = parsed
        if _users_store.verify(username, password):
            return AuthenticatedUser(username=username, source="users_file")
        raise _auth_error()

    if scheme == "bearer":
        username, password = _parse_bearer_credentials(auth_value)
        if username and _users_store.verify(username, password):
            return AuthenticatedUser(username=username, source="users_file")

        raise _auth_error()

    raise _auth_error()


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid username or password",
        headers={"WWW-Authenticate": "Basic"},
    )
