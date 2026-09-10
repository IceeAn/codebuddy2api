import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.password_hashing import create_password_hash
from src.sqlite_database import SQLiteDatabase
from src.users_store import (
    AUTH_STATE_INITIALIZED,
    AUTH_STATE_PENDING,
    SystemUserConfigurationError,
    UsersStore,
    escape_terminal_text,
    initialize_system_users,
    normalize_username,
    validate_new_password,
)


class SystemUserSchemaTests(unittest.TestCase):
    def test_fresh_database_records_pristine_authentication_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            with SQLiteDatabase(path).connect() as connection:
                state = connection.execute(
                    "SELECT state, legacy_install_detected, pending_username "
                    "FROM authentication_state WHERE id = 1"
                ).fetchone()

            self.assertEqual(tuple(state), ("uninitialized", 0, None))

    def test_v4_database_is_marked_as_legacy_install(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE existing(value TEXT)")
                connection.execute("PRAGMA user_version = 4")

            # 只验证 v5 迁移本身；v4 生产 schema 的其余表由既有迁移测试覆盖。
            with SQLiteDatabase(path).connect() as connection:
                state = connection.execute(
                    "SELECT state, legacy_install_detected FROM authentication_state"
                ).fetchone()

            self.assertEqual(tuple(state), ("uninitialized", 1))


class SystemUsersStoreTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.database_path = self.root / "data" / "codebuddy2api.sqlite3"
        self.users_path = self.root / "users.txt"
        self.credentials_path = self.root / "data" / "credentials"
        self.now = 1_700_000_000
        self.monotonic = 100.0
        self.store = UsersStore(
            database_path_provider=lambda: self.database_path,
            users_file_path_provider=lambda: self.users_path,
            credentials_path_provider=lambda: self.credentials_path,
            wall_clock=lambda: self.now,
            monotonic_clock=lambda: self.monotonic,
        )
        with SQLiteDatabase(self.database_path).connect():
            pass

    def tearDown(self):
        self._temporary_directory.cleanup()

    def _write_legacy(self, content: str) -> None:
        self.users_path.write_text(content, encoding="utf-8")
        os.chmod(self.users_path, 0o600)

    def test_username_and_password_policy_preserve_exact_unicode(self):
        self.assertEqual(normalize_username("  用户  "), "用户")
        for username in ("", " #admin", "a:b", "a\nb", "a\rb"):
            with self.subTest(username=username):
                with self.assertRaises(ValueError):
                    normalize_username(username)

        self.assertEqual(validate_new_password("八个字符密码！", minimum=1), "八个字符密码！")
        for password in ("", "a\x00b", "a" * 129):
            with self.subTest(password=password):
                with self.assertRaises(ValueError):
                    validate_new_password(password, minimum=1)
        with self.assertRaises(ValueError):
            validate_new_password(None, minimum=1)
        self.assertEqual(escape_terminal_text("a\x1bb"), "a\\u001bb")

    def test_relative_legacy_path_uses_current_working_directory(self):
        store = UsersStore(
            database_path_provider=lambda: self.database_path,
            users_file_path_provider=lambda: "legacy/users.txt",
            credentials_path_provider=lambda: self.credentials_path,
        )
        with mock.patch("src.users_store.Path.cwd", return_value=self.root):
            self.assertEqual(store._resolve_users_file(), self.root / "legacy/users.txt")

    def test_missing_authentication_state_fails_fast(self):
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute("DELETE FROM authentication_state")
        with self.assertRaisesRegex(SystemUserConfigurationError, "状态缺失"):
            self.store.state()

    def test_legacy_file_rejects_unsafe_file_shapes_and_invalid_utf8(self):
        unsafe = self.root / "unsafe"
        unsafe.mkdir()
        self.store._users_file_path_provider = lambda: unsafe
        with self.assertRaisesRegex(SystemUserConfigurationError, "普通文件"):
            self.store._read_legacy_users()

        source = self.root / "source.txt"
        source.write_text("# empty\n", encoding="utf-8")
        link = self.root / "link.txt"
        link.symlink_to(source)
        self.store._users_file_path_provider = lambda: link
        with self.assertRaisesRegex(SystemUserConfigurationError, "符号链接"):
            self.store._read_legacy_users()

        hardlink = self.root / "hardlink.txt"
        os.link(source, hardlink)
        self.store._users_file_path_provider = lambda: hardlink
        with self.assertRaisesRegex(SystemUserConfigurationError, "硬链接"):
            self.store._read_legacy_users()

        invalid = self.root / "invalid.txt"
        invalid.write_bytes(b"\xff")
        self.store._users_file_path_provider = lambda: invalid
        with self.assertRaisesRegex(SystemUserConfigurationError, "UTF-8"):
            self.store._read_legacy_users()

    def test_legacy_file_detects_race_and_invalid_username(self):
        self._write_legacy("# empty\n")
        real_stat = self.users_path.stat()
        changed = SimpleNamespace(
            st_mode=real_stat.st_mode,
            st_nlink=1,
            st_dev=real_stat.st_dev,
            st_ino=real_stat.st_ino + 1,
        )
        with (
            mock.patch("src.users_store.os.fstat", return_value=changed),
            self.assertRaisesRegex(SystemUserConfigurationError, "读取期间"),
        ):
            self.store._read_legacy_users()

        self._write_legacy(f":{create_password_hash('password')}\n")
        with self.assertRaisesRegex(SystemUserConfigurationError, "用户名无效"):
            self.store._read_legacy_users()

    def test_valid_legacy_file_is_imported_atomically_as_formal_users(self):
        alice_hash = create_password_hash("alice-password")
        replacement_hash = create_password_hash("replacement")
        self._write_legacy(
            f"# comment\n alice :{alice_hash}\nalice:{replacement_hash}\n"
        )

        result = self.store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="admin",
            bootstrap_password_explicit=False,
        )

        self.assertFalse(result.bootstrap_required)
        self.assertEqual(self.store.state(), AUTH_STATE_INITIALIZED)
        self.assertTrue(self.store.verify("alice", "replacement"))
        self.assertFalse(self.store.get_record("alice").password_change_required)

    def test_malformed_legacy_file_aborts_without_partial_import(self):
        self._write_legacy(
            f"alice:{create_password_hash('password')}\nmalformed\n"
        )

        with self.assertRaises(SystemUserConfigurationError):
            self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

        self.assertEqual(self.store.list_usernames(), ())

    def test_legacy_import_replaces_pending_and_detects_existing_conflicts(self):
        self.store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="admin",
            bootstrap_password_explicit=False,
        )
        migrated_hash = create_password_hash("migrated-password")
        self._write_legacy(f"alice:{migrated_hash}\n")
        self.store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="admin",
            bootstrap_password_explicit=False,
        )
        self.assertFalse(self.store.has_username("admin"))
        self.assertTrue(self.store.verify("alice", "migrated-password"))

        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "UPDATE authentication_state SET state = 'uninitialized'"
            )
        self._write_legacy(f"alice:{create_password_hash('different')}\n")
        with self.assertRaisesRegex(SystemUserConfigurationError, "冲突"):
            self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

        self._write_legacy(f"alice:{migrated_hash}\n")
        self.store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="admin",
            bootstrap_password_explicit=False,
        )

    def test_pristine_install_creates_expiring_pending_bootstrap(self):
        with self.assertLogs("src.users_store", level="WARNING") as captured:
            result = self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

        self.assertTrue(result.bootstrap_required)
        self.assertFalse(result.bootstrap_expired)
        self.assertEqual(self.store.state(), AUTH_STATE_PENDING)
        record = self.store.verify_record("admin", "admin")
        self.assertTrue(record.password_change_required)
        self.assertFalse(self.store.bootstrap_status().bootstrap_expired)
        self.assertEqual(
            captured.records[0].getMessage(),
            "引导账号“admin”正在使用内置默认密码；所有可访问本服务的客户端均可尝试登录，请立即修改密码。"
            "该引导账号将在本次服务启动 1 小时后失效，届时需重启服务。",
        )

        self.monotonic = 3700.0
        self.assertTrue(self.store.bootstrap_status().bootstrap_expired)

    def test_old_install_without_legacy_file_refuses_automatic_bootstrap(self):
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "UPDATE authentication_state SET legacy_install_detected = 1 WHERE id = 1"
            )
        with self.assertRaisesRegex(SystemUserConfigurationError, "set-user"):
            self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

    def test_existing_database_rows_or_credentials_prevent_automatic_bootstrap(self):
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "INSERT INTO user_settings VALUES ('alice', 'key', 'true')"
            )
        with self.assertRaisesRegex(SystemUserConfigurationError, "set-user"):
            self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute("DELETE FROM user_settings")
        self.credentials_path.mkdir(parents=True)
        (self.credentials_path / "alice").mkdir()
        with self.assertRaisesRegex(SystemUserConfigurationError, "set-user"):
            self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

        (self.credentials_path / "alice").rmdir()
        self.credentials_path.rmdir()
        self.store.set_user("alice", "alice-password")
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "UPDATE authentication_state SET state = 'uninitialized' WHERE id = 1"
            )
        with self.assertRaisesRegex(SystemUserConfigurationError, "set-user"):
            self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

    def test_disabled_or_invalid_bootstrap_fails_without_creating_user(self):
        with self.assertRaisesRegex(SystemUserConfigurationError, "已禁用"):
            self.store.initialize_service(
                bootstrap_enabled=False,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )
        for username, password in (("#bad", "admin"), ("admin", "")):
            with self.subTest(username=username, password=password):
                with self.assertRaises(SystemUserConfigurationError):
                    self.store.initialize_service(
                        bootstrap_enabled=True,
                        bootstrap_username=username,
                        bootstrap_password=password,
                        bootstrap_password_explicit=False,
                    )

    def test_pending_restart_preserves_or_replaces_account_from_config(self):
        with self.assertLogs("src.users_store", level="WARNING") as captured:
            self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="custom-password",
                bootstrap_password_explicit=True,
            )
        self.assertEqual(
            captured.records[0].getMessage(),
            "引导账号“admin”正在使用自定义初始密码且尚未完成首次修改；请立即修改密码。"
            "该引导账号将在本次服务启动 1 小时后失效，届时需重启服务。",
        )
        revision = self.store.get_record("admin").auth_revision
        result = self.store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="custom-password",
            bootstrap_password_explicit=True,
        )
        self.assertTrue(result.bootstrap_required)
        self.assertEqual(self.store.get_record("admin").auth_revision, revision)

        self.store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="root",
            bootstrap_password="replacement",
            bootstrap_password_explicit=True,
        )
        self.assertFalse(self.store.has_username("admin"))
        self.assertTrue(self.store.has_username("root"))

    def test_set_user_formalizes_pending_state_and_rotates_revision(self):
        self.store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="admin",
            bootstrap_password_explicit=False,
        )
        old_revision = self.store.get_record("admin").auth_revision

        self.store.set_user("admin", "new-password")

        record = self.store.get_record("admin")
        self.assertEqual(self.store.state(), AUTH_STATE_INITIALIZED)
        self.assertFalse(record.password_change_required)
        self.assertNotEqual(record.auth_revision, old_revision)
        self.assertTrue(self.store.verify("admin", "new-password"))

    def test_set_user_imports_legacy_first_and_replaces_other_pending_user(self):
        legacy_hash = create_password_hash("legacy-password")
        self._write_legacy(f"legacy:{legacy_hash}\n")
        self.store.set_user("alice", "alice-password")
        self.assertTrue(self.store.verify("legacy", "legacy-password"))

        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "UPDATE authentication_state SET state = 'pending_bootstrap', "
                "pending_username = 'legacy'"
            )
            connection.execute(
                "UPDATE system_users SET password_change_required = 1 "
                "WHERE username = 'legacy'"
            )
        self.store.set_user("bob", "bob-password")
        self.assertFalse(self.store.has_username("legacy"))
        self.assertTrue(self.store.has_username("bob"))

    def test_set_user_rereads_bootstrap_state_after_acquiring_write_lock(self):
        begin_waiting = threading.Event()
        bootstrap_committed = threading.Event()
        errors = []
        cli_thread = None
        real_connect = SQLiteDatabase.connect

        class DelayedConnection:
            def __init__(self, connection):
                self._connection = connection

            def execute(self, statement, parameters=()):
                if statement == "BEGIN IMMEDIATE":
                    begin_waiting.set()
                    if not bootstrap_committed.wait(10):
                        raise AssertionError("等待服务提交引导账号超时")
                return self._connection.execute(statement, parameters)

            def __getattr__(self, name):
                return getattr(self._connection, name)

        @contextmanager
        def delayed_connect(database, *, create=True):
            with real_connect(database, create=create) as connection:
                if threading.current_thread() is cli_thread:
                    yield DelayedConnection(connection)
                else:
                    yield connection

        def set_user():
            try:
                self.store.set_user("alice", "alice-password")
            except Exception as error:
                errors.append(error)

        cli_thread = threading.Thread(target=set_user)
        with mock.patch.object(SQLiteDatabase, "connect", delayed_connect):
            cli_thread.start()
            try:
                self.assertTrue(begin_waiting.wait(10))
                self.store.initialize_service(
                    bootstrap_enabled=True,
                    bootstrap_username="admin",
                    bootstrap_password="admin",
                    bootstrap_password_explicit=False,
                )
            finally:
                bootstrap_committed.set()
                cli_thread.join(10)

        self.assertFalse(cli_thread.is_alive())
        if errors:
            raise errors[0]
        self.assertEqual(self.store.state(), AUTH_STATE_INITIALIZED)
        self.assertEqual(self.store.list_usernames(), ("alice",))
        self.assertTrue(self.store.verify("alice", "alice-password"))

    def test_startup_does_not_overwrite_concurrent_cli_account(self):
        self._assert_startup_preserves_concurrent_cli_account()

    def test_migration_does_not_reimport_after_concurrent_cli_initialization(self):
        self._write_legacy(f"admin:{create_password_hash('legacy-password')}\n")
        self._assert_startup_preserves_concurrent_cli_account()

    def _assert_startup_preserves_concurrent_cli_account(self):
        # 独立实例模拟服务与 CLI 进程，避免进程内锁掩盖 SQLite 竞态。
        cli_store = UsersStore(
            database_path_provider=lambda: self.database_path,
            users_file_path_provider=lambda: self.users_path,
            credentials_path_provider=lambda: self.credentials_path,
        )
        begin_waiting = threading.Event()
        cli_committed = threading.Event()
        errors = []
        results = []
        service_thread = None
        real_connect = SQLiteDatabase.connect

        class DelayedConnection:
            def __init__(self, connection):
                self._connection = connection

            def execute(self, statement, parameters=()):
                if statement == "BEGIN IMMEDIATE":
                    begin_waiting.set()
                    if not cli_committed.wait(10):
                        raise AssertionError("等待 CLI 提交正式账号超时")
                return self._connection.execute(statement, parameters)

            def __getattr__(self, name):
                return getattr(self._connection, name)

        @contextmanager
        def delayed_connect(database, *, create=True):
            with real_connect(database, create=create) as connection:
                if threading.current_thread() is service_thread:
                    yield DelayedConnection(connection)
                else:
                    yield connection

        def initialize():
            try:
                results.append(self.store.initialize_service(
                    bootstrap_enabled=True,
                    bootstrap_username="admin",
                    bootstrap_password="admin",
                    bootstrap_password_explicit=False,
                ))
            except Exception as error:
                errors.append(error)

        service_thread = threading.Thread(target=initialize)
        with mock.patch.object(SQLiteDatabase, "connect", delayed_connect):
            service_thread.start()
            try:
                self.assertTrue(begin_waiting.wait(10))
                record = cli_store.set_user("admin", "formal-password")
            finally:
                cli_committed.set()
                service_thread.join(10)

        self.assertFalse(service_thread.is_alive())
        if errors:
            raise errors[0]
        self.assertTrue(self.store.verify("admin", "formal-password"))
        self.assertFalse(self.store.verify("admin", "admin"))
        self.assertEqual(self.store.get_record("admin"), record)
        self.assertEqual(self.store.state(), AUTH_STATE_INITIALIZED)
        self.assertEqual(self.store.list_usernames(), ("admin",))
        self.assertFalse(results[0].bootstrap_required)
        self.assertIsNone(self.store._bootstrap_deadline)

    def test_invalid_lookups_missing_database_and_cas_conflict_are_safe(self):
        self.assertIsNone(self.store.verify_record("#bad", "password"))
        self.assertIsNone(self.store.get_record("#bad"))
        missing_store = UsersStore(
            database_path_provider=lambda: self.root / "missing" / "db.sqlite3",
            users_file_path_provider=lambda: self.users_path,
            credentials_path_provider=lambda: self.credentials_path,
        )
        self.assertIsNone(missing_store.get_record("alice"))
        self.assertFalse(missing_store.has_users())
        self.assertEqual(missing_store.list_usernames(), ())

        record = self.store.set_user("alice", "alice-password")
        self.assertIsNone(
            self.store.replace_password(
                "alice",
                expected_revision=b"x" * 32,
                password_hash=create_password_hash("new-password"),
            )
        )
        self.assertIsNotNone(record)

    def test_delete_retains_last_user_and_removes_nonlast_user(self):
        self.store.set_user("alice", "password-a")
        self.store.set_user("bob", "password-b")

        self.assertTrue(self.store.delete_user("bob"))
        self.assertFalse(self.store.delete_user("missing"))
        with self.assertRaisesRegex(SystemUserConfigurationError, "最后一个"):
            self.store.delete_user("alice")

    def test_global_initialization_ignores_bootstrap_config_after_formalization(self):
        self.store.set_user("alice", "alice-password")
        with (
            mock.patch("src.users_store.users_store", self.store),
            mock.patch("config.get_bootstrap_enabled") as enabled,
            mock.patch("config.get_bootstrap_username") as username,
            mock.patch("config.get_bootstrap_password") as password,
        ):
            self.assertFalse(initialize_system_users().bootstrap_required)
        enabled.assert_not_called()
        username.assert_not_called()
        password.assert_not_called()

    def test_formal_state_ignores_legacy_file_with_warning(self):
        self.store.set_user("alice", "alice-password")
        self._write_legacy(f"bob:{create_password_hash('bob-password')}\n")

        with self.assertLogs("src.users_store", level="WARNING") as captured:
            result = self.store.initialize_service(
                bootstrap_enabled=True,
                bootstrap_username="admin",
                bootstrap_password="admin",
                bootstrap_password_explicit=False,
            )

        self.assertFalse(result.bootstrap_required)
        self.assertFalse(self.store.has_username("bob"))
        self.assertIn("旧用户文件已被忽略", captured.output[0])

    def test_global_initialization_reads_bootstrap_config_before_formalization(self):
        with (
            mock.patch("src.users_store.users_store", self.store),
            mock.patch("config.get_bootstrap_enabled", return_value=True) as enabled,
            mock.patch("config.get_bootstrap_username", return_value="root") as username,
            mock.patch("config.get_bootstrap_password", return_value="initial-password") as password,
            mock.patch(
                "config.is_bootstrap_password_explicit", return_value=True
            ) as explicit,
        ):
            result = initialize_system_users()

        self.assertTrue(result.bootstrap_required)
        self.assertTrue(self.store.verify("root", "initial-password"))
        enabled.assert_called_once_with()
        username.assert_called_once_with()
        password.assert_called_once_with()
        explicit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
