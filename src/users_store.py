"""用户密码文件存储。"""
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from config import get_users_file_path
from .auth_types import DUMMY_PASSWORD_HASH
from .password_hashing import is_supported_password_hash, verify_password

logger = logging.getLogger(__name__)


class UsersFileConfigurationError(RuntimeError):
    """系统用户文件配置无效，服务启动必须失败。"""


class UsersFileStore:
    """从 secrets/users.txt 加载用户名和密码哈希。"""

    def __init__(self):
        self._users: Dict[str, str] = {}
        self._loaded_path: Optional[Path] = None
        self._loaded_mtime: Optional[float] = None
        self._cache_lock = threading.RLock()

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
                if not is_supported_password_hash(password_hash.strip()):
                    logger.warning("Ignoring invalid password hash on line %s in %s", line_number, users_file)
                    continue

                users[username.strip()] = password_hash.strip()

        self._users = users
        self._loaded_path = users_file
        self._loaded_mtime = current_mtime
        logger.info("Loaded %s user(s) from %s", len(users), users_file)

    def verify(self, username: str, password: str) -> bool:
        with self._cache_lock:
            self._load_if_needed()
            password_hash = self._users.get(username)
        if not password_hash:
            verify_password(password, DUMMY_PASSWORD_HASH)
            return False
        return verify_password(password, password_hash)

    def has_users_file(self) -> bool:
        with self._cache_lock:
            self._load_if_needed()
            return bool(self._users)

    def has_username(self, username: str) -> bool:
        with self._cache_lock:
            self._load_if_needed()
            return username in self._users

    def validate_configured_users_file(self) -> None:
        """启动期校验系统用户文件，避免服务以不可登录状态运行。"""
        users_file = self._resolve_users_file()
        if not users_file.exists():
            raise UsersFileConfigurationError(
                f"Authentication users file not found: {users_file}"
            )
        if not users_file.is_file():
            raise UsersFileConfigurationError(
                f"Authentication users file is not a regular file: {users_file}"
            )

        with self._cache_lock:
            self._load_if_needed()
            has_users = bool(self._users)
        if not has_users:
            raise UsersFileConfigurationError(
                f"Authentication users file has no valid users: {users_file}"
            )


users_store = UsersFileStore()


def validate_configured_users_file() -> None:
    users_store.validate_configured_users_file()
