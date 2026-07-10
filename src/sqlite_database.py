"""SQLite 数据库连接、schema 初始化与安全权限管理。"""
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union
from urllib.parse import quote

DATABASE_FILENAME = "codebuddy2api.sqlite3"
SCHEMA_VERSION = 2

_SCHEMA_LOCK = threading.RLock()
_SCHEMA_V1_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        name TEXT NOT NULL,
        key_digest BLOB NOT NULL,
        preview TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_used_at INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_api_keys_username ON api_keys(username)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_digest ON api_keys(key_digest)",
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        username TEXT NOT NULL,
        setting_key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        PRIMARY KEY (username, setting_key)
    )
    """,
)

_SCHEMA_V2_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS usage_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        occurred_at INTEGER NOT NULL,
        source TEXT NOT NULL CHECK (
            source IN ('external_api', 'admin_playground', 'credential_test')
        ),
        requested_model TEXT NOT NULL,
        upstream_model TEXT,
        api_key_id TEXT,
        api_key_name TEXT,
        credential_id TEXT,
        credential_label TEXT,
        outcome TEXT NOT NULL CHECK (
            outcome IN ('success', 'failure', 'cancelled')
        ),
        http_status INTEGER,
        result_status INTEGER,
        error_type TEXT,
        client_stream INTEGER CHECK (client_stream IS NULL OR client_stream IN (0, 1)),
        thinking_mode TEXT,
        message_count INTEGER,
        tool_count INTEGER,
        request_bytes INTEGER,
        response_bytes INTEGER,
        retry_count INTEGER,
        tool_call_count INTEGER,
        finish_reason TEXT,
        input_tokens INTEGER,
        output_tokens INTEGER,
        total_tokens INTEGER,
        reasoning_tokens INTEGER,
        cache_hit_tokens INTEGER,
        cache_miss_tokens INTEGER,
        cache_write_tokens INTEGER,
        credit REAL,
        duration_ms REAL,
        first_event_ms REAL,
        first_output_ms REAL,
        first_reasoning_ms REAL,
        first_content_ms REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_events_user_id ON usage_events(username, id)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_user_time ON usage_events(username, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_usage_events_occurred_at_id ON usage_events(occurred_at, id)",
    """
    CREATE TABLE IF NOT EXISTS usage_hourly (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        hour INTEGER NOT NULL,
        source TEXT NOT NULL,
        model TEXT NOT NULL,
        api_key_id TEXT NOT NULL,
        api_key_name TEXT NOT NULL,
        credential_id TEXT NOT NULL,
        credential_label TEXT NOT NULL,
        outcome TEXT NOT NULL,
        request_count INTEGER NOT NULL DEFAULT 0,
        success_count INTEGER NOT NULL DEFAULT 0,
        failure_count INTEGER NOT NULL DEFAULT 0,
        cancelled_count INTEGER NOT NULL DEFAULT 0,
        usage_known_count INTEGER NOT NULL DEFAULT 0,
        input_tokens_sum INTEGER NOT NULL DEFAULT 0,
        input_tokens_known_count INTEGER NOT NULL DEFAULT 0,
        output_tokens_sum INTEGER NOT NULL DEFAULT 0,
        output_tokens_known_count INTEGER NOT NULL DEFAULT 0,
        total_tokens_sum INTEGER NOT NULL DEFAULT 0,
        total_tokens_known_count INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens_sum INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens_known_count INTEGER NOT NULL DEFAULT 0,
        cache_hit_tokens_sum INTEGER NOT NULL DEFAULT 0,
        cache_hit_tokens_known_count INTEGER NOT NULL DEFAULT 0,
        cache_miss_tokens_sum INTEGER NOT NULL DEFAULT 0,
        cache_miss_tokens_known_count INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens_sum INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens_known_count INTEGER NOT NULL DEFAULT 0,
        credit_sum REAL NOT NULL DEFAULT 0,
        credit_known_count INTEGER NOT NULL DEFAULT 0,
        request_bytes_sum INTEGER NOT NULL DEFAULT 0,
        request_bytes_known_count INTEGER NOT NULL DEFAULT 0,
        response_bytes_sum INTEGER NOT NULL DEFAULT 0,
        response_bytes_known_count INTEGER NOT NULL DEFAULT 0,
        retry_count_sum INTEGER NOT NULL DEFAULT 0,
        retry_count_known_count INTEGER NOT NULL DEFAULT 0,
        tool_call_count_sum INTEGER NOT NULL DEFAULT 0,
        tool_call_count_known_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE (
            username, hour, source, model, api_key_id, api_key_name,
            credential_id, credential_label, outcome
        )
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_hourly_user_hour ON usage_hourly(username, hour)",
    """
    CREATE TABLE IF NOT EXISTS usage_latency_histogram (
        hourly_id INTEGER NOT NULL,
        metric TEXT NOT NULL CHECK (metric IN ('total', 'first_output')),
        bucket_index INTEGER NOT NULL,
        sample_count INTEGER NOT NULL,
        PRIMARY KEY (hourly_id, metric, bucket_index),
        FOREIGN KEY (hourly_id) REFERENCES usage_hourly(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS usage_retention_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        detail_cutoff INTEGER NOT NULL
    )
    """,
)


def resolve_database_path(data_dir: Union[str, Path], cwd: Union[str, Path, None] = None) -> Path:
    """基于数据目录解析统一 SQLite 数据库路径。"""
    directory = Path(data_dir)
    if not directory.is_absolute():
        directory = Path(cwd) if cwd is not None else Path.cwd()
        directory = directory / data_dir
    return directory / DATABASE_FILENAME


class SQLiteDatabase:
    """为短生命周期连接配置事务、安全路径和统一 schema。"""

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def _connection_uri(self, *, create: bool = True) -> str:
        absolute_path = os.path.abspath(self.path)
        mode = "rwc" if create else "rw"
        return f"file:{quote(absolute_path, safe='/')}?mode={mode}"

    def _initialize_schema(self, connection: sqlite3.Connection) -> None:
        with _SCHEMA_LOCK:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise RuntimeError(
                    f"Unsupported SQLite schema version {version}; expected {SCHEMA_VERSION}"
                )

            journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                raise RuntimeError(
                    f"SQLite WAL mode is required; actual journal mode is {journal_mode}"
                )

            if version < SCHEMA_VERSION:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    statements = _SCHEMA_V2_STATEMENTS
                    if version == 0:
                        statements = _SCHEMA_V1_STATEMENTS + statements
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

    def _prepare_data_directory(self, *, create: bool = True) -> None:
        """创建并安全打开数据目录，拒绝目录本身是符号链接。"""
        directory = self.path.parent
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink():
            raise RuntimeError(f"SQLite data directory must not be a symbolic link: {directory}")

        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        descriptor = os.open(directory, flags)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise RuntimeError(f"SQLite data directory must be a directory: {directory}")
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)

    def _prepare_database_file(self, *, create: bool = True) -> None:
        """在 SQLite 创建 WAL 侧文件前，以 0600 安全创建或收紧主文件权限。"""
        flags = (
            os.O_RDWR
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        if create:
            flags |= os.O_CREAT
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except IsADirectoryError as error:
            raise RuntimeError(f"SQLite database must be a regular file: {self.path}") from error
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise RuntimeError(f"SQLite database must be a regular file: {self.path}")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)

    def _tighten_existing_sidecar_permissions(self) -> None:
        """拒绝不安全的 sidecar，并将已有普通文件权限收紧到 0600。"""
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar_path = Path(f"{self.path}{suffix}")
            if sidecar_path.is_symlink():
                raise RuntimeError(
                    f"SQLite sidecar must not be a symbolic link: {sidecar_path}"
                )

            common_flags = (
                getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0)
            )
            try:
                descriptor = os.open(sidecar_path, os.O_RDONLY | common_flags)
            except FileNotFoundError:
                continue
            except PermissionError:
                try:
                    descriptor = os.open(sidecar_path, os.O_WRONLY | common_flags)
                except FileNotFoundError:
                    continue
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise RuntimeError(
                        f"SQLite sidecar must be a regular file: {sidecar_path}"
                    )
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)

    @contextmanager
    def connect(self, *, create: bool = True) -> Iterator[sqlite3.Connection]:
        """打开一个提交成功、异常回滚并始终关闭的数据库连接。"""
        self._prepare_data_directory(create=create)
        if self.path.is_symlink():
            raise RuntimeError(f"SQLite database must not be a symbolic link: {self.path}")
        self._prepare_database_file(create=create)
        self._tighten_existing_sidecar_permissions()

        connection = sqlite3.connect(
            self._connection_uri(create=create),
            uri=True,
            timeout=5,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            self._initialize_schema(connection)
            self._tighten_existing_sidecar_permissions()
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
