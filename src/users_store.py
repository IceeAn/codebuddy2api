"""SQLite 系统账号存储、旧用户文件迁移与初始账号生命周期。"""

import logging
import os
import secrets
import stat
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from config import get_codebuddy_creds_dir, get_database_path, get_users_file_path

from .auth_types import DUMMY_PASSWORD_HASH
from .password_hashing import create_password_hash, is_supported_password_hash, verify_password
from .sqlite_database import SQLiteDatabase

logger = logging.getLogger(__name__)

AUTH_STATE_UNINITIALIZED = "uninitialized"
AUTH_STATE_PENDING = "pending_bootstrap"
AUTH_STATE_INITIALIZED = "initialized"
BOOTSTRAP_LIFETIME_SECONDS = 60 * 60


class SystemUserConfigurationError(RuntimeError):
    """账号初始化或账号管理配置无效。"""


@dataclass(frozen=True)
class UserRecord:
    """不含密码哈希的账号快照。"""

    username: str
    password_change_required: bool
    auth_revision: bytes
    updated_at: int


@dataclass(frozen=True)
class BootstrapStatus:
    """供登录页与启动流程使用的初始账号状态。"""

    bootstrap_required: bool
    bootstrap_expired: bool


def normalize_username(username: str) -> str:
    """沿用既有大小写敏感语义，仅去除首尾空白并拒绝歧义分隔符。"""
    normalized = str(username).strip()
    if (
        not normalized
        or normalized.startswith("#")
        or ":" in normalized
        or "\r" in normalized
        or "\n" in normalized
    ):
        raise ValueError("用户名不能为空，且不能以 # 开头或包含冒号、回车、换行")
    return normalized


def validate_new_password(password: str, *, minimum: int) -> str:
    """验证新写入密码；不修剪、不折叠大小写，也不做 Unicode 归一化。"""
    if not isinstance(password, str):
        raise ValueError("密码必须是字符串")
    length = len(password)
    if length < minimum or length > 128:
        raise ValueError(f"密码必须为 {minimum} 至 128 个字符")
    if any(unicodedata.category(character) == "Cc" for character in password):
        raise ValueError("密码不能包含控制字符")
    return password


def escape_terminal_text(value: str) -> str:
    """转义控制字符，避免日志及终端输出被账号名注入控制序列。"""
    return "".join(
        f"\\u{ord(character):04x}"
        if unicodedata.category(character) == "Cc"
        else character
        for character in str(value)
    )


class UsersStore:
    """以 SQLite 为唯一运行时权威来源管理系统账号。"""

    def __init__(
        self,
        *,
        database_path_provider: Optional[Callable[[], Path]] = None,
        users_file_path_provider: Optional[Callable[[], str | Path]] = None,
        credentials_path_provider: Optional[Callable[[], str | Path]] = None,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._database_path_provider = database_path_provider or get_database_path
        self._users_file_path_provider = users_file_path_provider or get_users_file_path
        self._credentials_path_provider = credentials_path_provider or get_codebuddy_creds_dir
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._bootstrap_deadline: Optional[float] = None
        self._runtime_lock = threading.RLock()

    def _database(self) -> SQLiteDatabase:
        return SQLiteDatabase(self._database_path_provider())

    def _resolve_users_file(self) -> Path:
        users_file = Path(self._users_file_path_provider())
        if not users_file.is_absolute():
            users_file = Path.cwd() / users_file
        return users_file

    @staticmethod
    def _record_from_row(row) -> UserRecord:
        return UserRecord(
            username=str(row["username"]),
            password_change_required=bool(row["password_change_required"]),
            auth_revision=bytes(row["auth_revision"]),
            updated_at=int(row["updated_at"]),
        )

    @staticmethod
    def _state_row(connection):
        row = connection.execute(
            "SELECT state, legacy_install_detected, pending_username "
            "FROM authentication_state WHERE id = 1"
        ).fetchone()
        if row is None:
            raise SystemUserConfigurationError("账号初始化状态缺失")
        return row

    def state(self) -> str:
        with self._database().connect(create=False) as connection:
            return str(self._state_row(connection)["state"])

    def _read_legacy_users(self) -> Optional[Dict[str, str]]:
        """严格读取首次迁移文件；无文件或仅空白/注释时返回 None。"""
        path = self._resolve_users_file()
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise SystemUserConfigurationError(
                f"旧用户文件必须是普通文件且不能是符号链接：{escape_terminal_text(str(path))}"
            )
        if path_stat.st_nlink != 1:
            raise SystemUserConfigurationError(
                f"旧用户文件不能有多个硬链接：{escape_terminal_text(str(path))}"
            )
        if path_stat.st_mode & 0o077:
            logger.warning(
                "旧用户文件权限过宽，将继续迁移：%s",
                escape_terminal_text(str(path)),
            )

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(path, flags)
        try:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_nlink != 1
                or (opened_stat.st_dev, opened_stat.st_ino)
                != (path_stat.st_dev, path_stat.st_ino)
            ):
                raise SystemUserConfigurationError("旧用户文件在读取期间发生变化")
            with os.fdopen(descriptor, "r", encoding="utf-8", errors="strict") as stream:
                descriptor = -1
                lines = stream.readlines()
        except UnicodeDecodeError as error:
            raise SystemUserConfigurationError("旧用户文件不是有效的 UTF-8") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        users: Dict[str, str] = {}
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            raw_username, separator, raw_hash = stripped.partition(":")
            try:
                username = normalize_username(raw_username)
            except ValueError as error:
                raise SystemUserConfigurationError(
                    f"旧用户文件第 {line_number} 行用户名无效"
                ) from error
            password_hash = raw_hash.strip()
            if not separator or not password_hash or not is_supported_password_hash(password_hash):
                raise SystemUserConfigurationError(
                    f"旧用户文件第 {line_number} 行密码哈希无效"
                )
            users[username] = password_hash
        return users or None

    def _has_legacy_file_marker(self) -> bool:
        try:
            self._resolve_users_file().lstat()
        except FileNotFoundError:
            return False
        return True

    def _has_existing_user_data(self, connection) -> bool:
        if connection.execute("SELECT 1 FROM system_users LIMIT 1").fetchone() is not None:
            return True
        for table in (
            "api_keys",
            "user_settings",
            "usage_events",
            "usage_hourly",
            "usage_known_models",
            "credential_daily_checkins",
        ):
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
                return True
        credentials_path = Path(self._credentials_path_provider())
        return credentials_path.exists() and next(credentials_path.iterdir(), None) is not None

    def _import_legacy_users(self, connection, users: Dict[str, str], *, replace_pending: bool) -> None:
        state_row = self._state_row(connection)
        pending_username = state_row["pending_username"]
        if replace_pending and pending_username is not None:
            connection.execute("DELETE FROM system_users WHERE username = ?", (pending_username,))

        now = int(self._wall_clock())
        for username, password_hash in users.items():
            existing = connection.execute(
                "SELECT password_hash FROM system_users WHERE username = ?", (username,)
            ).fetchone()
            if existing is not None:
                if existing["password_hash"] != password_hash:
                    raise SystemUserConfigurationError(
                        "旧用户文件与 SQLite 中同名账号的密码哈希冲突"
                    )
                continue
            connection.execute(
                """
                INSERT INTO system_users(
                    username, password_hash, password_change_required,
                    auth_revision, updated_at
                ) VALUES (?, ?, 0, ?, ?)
                """,
                (username, password_hash, secrets.token_bytes(32), now),
            )
        connection.execute(
            "UPDATE authentication_state SET state = ?, pending_username = NULL WHERE id = 1",
            (AUTH_STATE_INITIALIZED,),
        )

    def initialize_service(
        self,
        *,
        bootstrap_enabled: bool,
        bootstrap_username: str,
        bootstrap_password: str,
        bootstrap_password_explicit: bool,
    ) -> BootstrapStatus:
        """完成一次启动期迁移/引导；成功后设置本进程的一小时硬期限。"""
        with self._runtime_lock:
            with self._database().connect() as connection:
                # 与 CLI 串行化状态读取和写入，禁止用旧状态覆盖正式账号。
                connection.execute("BEGIN IMMEDIATE")
                state_row = self._state_row(connection)
                state = str(state_row["state"])
                if state == AUTH_STATE_INITIALIZED:
                    self._bootstrap_deadline = None
                    if self._has_legacy_file_marker():
                        logger.warning(
                            "账号迁移已完成，旧用户文件已被忽略，请由运维人员确认后移除：%s",
                            escape_terminal_text(str(self._resolve_users_file())),
                        )
                    return BootstrapStatus(False, False)

                legacy_users = self._read_legacy_users()
                if legacy_users is not None:
                    self._import_legacy_users(
                        connection, legacy_users, replace_pending=state == AUTH_STATE_PENDING
                    )
                    self._bootstrap_deadline = None
                    logger.info(
                        "已从旧用户文件迁移 %s 个系统账号：%s",
                        len(legacy_users),
                        escape_terminal_text(str(self._resolve_users_file())),
                    )
                    return BootstrapStatus(False, False)

                if state == AUTH_STATE_UNINITIALIZED and (
                    bool(state_row["legacy_install_detected"])
                    or self._has_existing_user_data(connection)
                ):
                    raise SystemUserConfigurationError(
                        "检测到既有安装但没有可迁移的旧用户文件；请使用 manage_users.py set-user 创建账号"
                    )
                if not bootstrap_enabled:
                    raise SystemUserConfigurationError(
                        "初始账号已禁用且系统没有正式账号；请使用 manage_users.py set-user 创建账号"
                    )

                try:
                    username = normalize_username(bootstrap_username)
                    password = validate_new_password(bootstrap_password, minimum=1)
                except ValueError as error:
                    raise SystemUserConfigurationError(str(error)) from error

                pending_username = state_row["pending_username"]
                existing = None
                if pending_username == username:
                    existing = connection.execute(
                        "SELECT password_hash FROM system_users WHERE username = ?", (username,)
                    ).fetchone()
                unchanged = existing is not None and verify_password(password, existing["password_hash"])
                if not unchanged:
                    password_hash = create_password_hash(password)
                    if pending_username is not None:
                        connection.execute(
                            "DELETE FROM system_users WHERE username = ?", (pending_username,)
                        )
                    connection.execute(
                        """
                        INSERT INTO system_users(
                            username, password_hash, password_change_required,
                            auth_revision, updated_at
                        ) VALUES (?, ?, 1, ?, ?)
                        ON CONFLICT(username) DO UPDATE SET
                            password_hash = excluded.password_hash,
                            password_change_required = 1,
                            auth_revision = excluded.auth_revision,
                            updated_at = excluded.updated_at
                        """,
                        (username, password_hash, secrets.token_bytes(32), int(self._wall_clock())),
                    )
                    connection.execute(
                        "UPDATE authentication_state SET state = ?, pending_username = ? WHERE id = 1",
                        (AUTH_STATE_PENDING, username),
                    )

                self._bootstrap_deadline = self._monotonic_clock() + BOOTSTRAP_LIFETIME_SECONDS
                escaped_username = escape_terminal_text(username)
                if bootstrap_password_explicit:
                    warning = (
                        f"引导账号“{escaped_username}”正在使用自定义初始密码且尚未完成首次修改；"
                        "请立即修改密码。"
                    )
                else:
                    warning = (
                        f"引导账号“{escaped_username}”正在使用内置默认密码；"
                        "所有可访问本服务的客户端均可尝试登录，请立即修改密码。"
                    )
                logger.warning(
                    "%s该引导账号将在本次服务启动 1 小时后失效，届时需重启服务。",
                    warning,
                )
                return BootstrapStatus(True, False)

    def bootstrap_status(self) -> BootstrapStatus:
        with self._database().connect(create=False) as connection:
            state = str(self._state_row(connection)["state"])
        if state != AUTH_STATE_PENDING:
            return BootstrapStatus(False, False)
        with self._runtime_lock:
            expired = self._bootstrap_deadline is None or self._monotonic_clock() >= self._bootstrap_deadline
        return BootstrapStatus(True, expired)

    def is_bootstrap_expired(self, username: str) -> bool:
        with self._database().connect(create=False) as connection:
            state_row = self._state_row(connection)
        return (
            state_row["state"] == AUTH_STATE_PENDING
            and state_row["pending_username"] == username
            and self.bootstrap_status().bootstrap_expired
        )

    def verify_record(self, username: str, password: str) -> Optional[UserRecord]:
        try:
            normalized = normalize_username(username)
        except ValueError:
            normalized = ""
        with self._database().connect(create=False) as connection:
            row = connection.execute(
                "SELECT username, password_hash, password_change_required, "
                "auth_revision, updated_at FROM system_users WHERE username = ?", (normalized,)
            ).fetchone()
        password_hash = row["password_hash"] if row is not None else DUMMY_PASSWORD_HASH
        if not verify_password(password, password_hash) or row is None:
            return None
        return self._record_from_row(row)

    def verify(self, username: str, password: str) -> bool:
        return self.verify_record(username, password) is not None

    def get_record(self, username: str) -> Optional[UserRecord]:
        try:
            normalized = normalize_username(username)
        except ValueError:
            return None
        database = self._database()
        if not database.path.exists():
            return None
        with database.connect(create=False) as connection:
            row = connection.execute(
                "SELECT username, password_change_required, auth_revision, updated_at "
                "FROM system_users WHERE username = ?", (normalized,)
            ).fetchone()
        return None if row is None else self._record_from_row(row)

    def has_username(self, username: str) -> bool:
        return self.get_record(username) is not None

    def has_users(self) -> bool:
        database = self._database()
        if not database.path.exists():
            return False
        with database.connect(create=False) as connection:
            return connection.execute("SELECT 1 FROM system_users LIMIT 1").fetchone() is not None

    def list_usernames(self) -> Tuple[str, ...]:
        database = self._database()
        if not database.path.exists():
            return ()
        with database.connect(create=False) as connection:
            rows = connection.execute("SELECT username FROM system_users ORDER BY username").fetchall()
        return tuple(str(row["username"]) for row in rows)

    def list_records(self) -> Tuple[UserRecord, ...]:
        with self._database().connect(create=False) as connection:
            rows = connection.execute(
                "SELECT username, password_change_required, auth_revision, updated_at "
                "FROM system_users ORDER BY username"
            ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

    def set_user(self, username: str, password: str) -> UserRecord:
        normalized = normalize_username(username)
        validate_new_password(password, minimum=1)
        password_hash = create_password_hash(password)
        with self._database().connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state_row = self._state_row(connection)
            legacy_users = (
                self._read_legacy_users()
                if state_row["state"] == AUTH_STATE_UNINITIALIZED
                else None
            )
            if legacy_users is not None:
                self._import_legacy_users(connection, legacy_users, replace_pending=False)
                state_row = self._state_row(connection)
            pending_username = state_row["pending_username"]
            if state_row["state"] == AUTH_STATE_PENDING and pending_username != normalized:
                connection.execute("DELETE FROM system_users WHERE username = ?", (pending_username,))
            now = int(self._wall_clock())
            revision = secrets.token_bytes(32)
            connection.execute(
                """
                INSERT INTO system_users(
                    username, password_hash, password_change_required,
                    auth_revision, updated_at
                ) VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    password_change_required = 0,
                    auth_revision = excluded.auth_revision,
                    updated_at = excluded.updated_at
                """,
                (normalized, password_hash, revision, now),
            )
            connection.execute(
                "UPDATE authentication_state SET state = ?, pending_username = NULL WHERE id = 1",
                (AUTH_STATE_INITIALIZED,),
            )
        with self._runtime_lock:
            self._bootstrap_deadline = None
        return UserRecord(normalized, False, revision, now)

    def replace_password(
        self,
        username: str,
        *,
        expected_revision: bytes,
        password_hash: str,
    ) -> Optional[UserRecord]:
        """以认证代次 CAS 更新密码；并发情况下仅首个提交成功。"""
        now = int(self._wall_clock())
        revision = secrets.token_bytes(32)
        with self._database().connect(create=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE system_users
                SET password_hash = ?, password_change_required = 0,
                    auth_revision = ?, updated_at = ?
                WHERE username = ? AND auth_revision = ?
                """,
                (password_hash, revision, now, username, expected_revision),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute(
                "UPDATE authentication_state SET state = ?, pending_username = NULL "
                "WHERE id = 1 AND state = ? AND pending_username = ?",
                (AUTH_STATE_INITIALIZED, AUTH_STATE_PENDING, username),
            )
        with self._runtime_lock:
            self._bootstrap_deadline = None
        return UserRecord(username, False, revision, now)

    def delete_user(self, username: str) -> bool:
        normalized = normalize_username(username)
        with self._database().connect(create=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = int(connection.execute("SELECT COUNT(*) FROM system_users").fetchone()[0])
            exists = connection.execute(
                "SELECT 1 FROM system_users WHERE username = ?", (normalized,)
            ).fetchone()
            if exists is None:
                return False
            if count <= 1:
                raise SystemUserConfigurationError("不能删除最后一个系统账号")
            connection.execute("DELETE FROM system_users WHERE username = ?", (normalized,))
        return True

    def reset_runtime(self) -> None:
        with self._runtime_lock:
            self._bootstrap_deadline = None


users_store = UsersStore()


def initialize_system_users() -> BootstrapStatus:
    """从启动配置初始化全局账号存储；正式初始化后配置值完全不再校验。"""
    from config import (
        get_bootstrap_enabled,
        get_bootstrap_password,
        get_bootstrap_username,
        is_bootstrap_password_explicit,
    )

    state = users_store.state()
    if state == AUTH_STATE_INITIALIZED:
        return users_store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="",
            bootstrap_password="",
            bootstrap_password_explicit=False,
        )
    return users_store.initialize_service(
        bootstrap_enabled=get_bootstrap_enabled(),
        bootstrap_username=get_bootstrap_username(),
        bootstrap_password=get_bootstrap_password(),
        bootstrap_password_explicit=is_bootstrap_password_explicit(),
    )
