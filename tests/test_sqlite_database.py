import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.sqlite_database import DATABASE_FILENAME, SQLiteDatabase, resolve_database_path
from src.user_settings_schema import coerce_user_setting, sanitize_user_settings
from src.user_settings_store import UserSettingsStore


class SQLiteDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self._temp_dir.name)
        self.database_path = self.temp_path / DATABASE_FILENAME

    def tearDown(self):
        self._temp_dir.cleanup()

    def test_resolve_database_path_handles_absolute_and_relative_directories(self):
        self.assertEqual(resolve_database_path(self.temp_path), self.database_path)
        self.assertEqual(
            resolve_database_path("data", cwd=self.temp_path),
            self.temp_path / "data" / DATABASE_FILENAME,
        )

    def test_connection_initializes_schema_version_and_private_permissions(self):
        data_directory = self.temp_path / "data"
        data_directory.mkdir(mode=0o755)
        database_path = data_directory / DATABASE_FILENAME
        database = SQLiteDatabase(database_path)

        with database.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            sidecar_modes = {
                path.name: oct(os.stat(path).st_mode & 0o777)
                for path in data_directory.iterdir()
                if path.name != DATABASE_FILENAME
            }

        self.assertEqual(version, 3)
        self.assertTrue({
            "api_keys",
            "user_settings",
            "usage_events",
            "usage_hourly",
            "usage_latency_histogram",
            "usage_retention_state",
            "credential_daily_checkins",
        }.issubset(tables))
        with sqlite3.connect(database_path) as connection:
            api_key_columns = {
                row[1]: row[2]
                for row in connection.execute("PRAGMA table_info(api_keys)")
            }
            indexes = {
                row[1]: row
                for row in connection.execute("PRAGMA index_list(api_keys)")
            }
            query_plan = " ".join(
                row[3]
                for row in connection.execute(
                    """
                    EXPLAIN QUERY PLAN
                    SELECT id, username, last_used_at
                    FROM api_keys
                    WHERE key_digest = ?
                    """,
                    (b"\0" * 32,),
                )
            )
        self.assertEqual(api_key_columns["key_digest"], "BLOB")
        self.assertNotIn("key_hash", api_key_columns)
        self.assertIn("idx_api_keys_digest", indexes)
        self.assertEqual(indexes["idx_api_keys_digest"][2], 1)
        self.assertIn("USING INDEX idx_api_keys_digest", query_plan)
        self.assertEqual(oct(os.stat(data_directory).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(database_path).st_mode & 0o777), "0o600")
        self.assertTrue(sidecar_modes)
        self.assertEqual(set(sidecar_modes.values()), {"0o600"})

        with sqlite3.connect(database_path) as connection:
            usage_indexes = {
                row[1] for row in connection.execute("PRAGMA index_list(usage_events)")
            }
            cleanup_plan = " ".join(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM usage_events "
                    "WHERE occurred_at < ? ORDER BY occurred_at, id LIMIT ?",
                    (0, 1000),
                )
            )
        self.assertIn("idx_usage_events_occurred_at_id", usage_indexes)
        self.assertIn("idx_usage_events_occurred_at_id", cleanup_plan)

    def test_existing_only_connection_never_creates_a_missing_database(self):
        database = SQLiteDatabase(self.database_path)

        with self.assertRaises(FileNotFoundError):
            with database.connect(create=False):
                pass

        self.assertFalse(self.database_path.exists())

    def test_connection_restores_wal_for_current_schema(self):
        database = SQLiteDatabase(self.database_path)
        with database.connect():
            pass

        with sqlite3.connect(self.database_path) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "delete")

        with database.connect() as connection:
            restored_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

        self.assertEqual(restored_mode.lower(), "wal")

    def test_connection_atomically_migrates_v1_and_preserves_existing_rows(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TABLE api_keys (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    name TEXT NOT NULL,
                    key_digest BLOB NOT NULL,
                    preview TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_used_at INTEGER
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE user_settings (
                    username TEXT NOT NULL,
                    setting_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    PRIMARY KEY (username, setting_key)
                )
                """
            )
            connection.execute(
                "INSERT INTO user_settings VALUES (?, ?, ?)",
                ("alice", "enabled", "true"),
            )
            connection.execute("PRAGMA user_version = 1")

        with SQLiteDatabase(self.database_path).connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            value = connection.execute(
                "SELECT value_json FROM user_settings WHERE username = 'alice'"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertEqual(version, 3)
        self.assertEqual(value, "true")
        self.assertTrue({
            "usage_events",
            "usage_hourly",
            "usage_latency_histogram",
            "usage_retention_state",
            "credential_daily_checkins",
        }.issubset(tables))

    def test_connection_atomically_migrates_v2_and_preserves_existing_rows(self):
        database = SQLiteDatabase(self.database_path)
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO user_settings VALUES (?, ?, ?)",
                ("alice", "enabled", "true"),
            )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP TABLE credential_daily_checkins")
            connection.execute("PRAGMA user_version = 2")

        with database.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            value = connection.execute(
                "SELECT value_json FROM user_settings WHERE username = 'alice'"
            ).fetchone()[0]
            checkin_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'credential_daily_checkins'"
            ).fetchone()

        self.assertEqual(version, 3)
        self.assertEqual(value, "true")
        self.assertIsNotNone(checkin_table)

    def test_v1_migration_rolls_back_all_new_objects_when_ddl_fails(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "CREATE TABLE user_settings (username TEXT, setting_key TEXT, value_json TEXT)"
            )
            connection.execute(
                "INSERT INTO user_settings VALUES ('alice', 'key', 'value')"
            )
            connection.execute("PRAGMA user_version = 1")

        with sqlite3.connect(self.database_path) as connection:
            def deny_hourly_table(action, argument, *_args):
                if action == sqlite3.SQLITE_CREATE_TABLE and argument == "usage_hourly":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(deny_hourly_table)
            with self.assertRaises(sqlite3.DatabaseError):
                SQLiteDatabase(self.database_path)._initialize_schema(connection)
            connection.set_authorizer(None)

            version = connection.execute("PRAGMA user_version").fetchone()[0]
            value = connection.execute(
                "SELECT value_json FROM user_settings WHERE username = 'alice'"
            ).fetchone()[0]
            stats_objects = connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'usage_%'"
            ).fetchall()

        self.assertEqual(version, 1)
        self.assertEqual(value, "value")
        self.assertEqual(stats_objects, [])

    def test_connection_fails_when_wal_cannot_be_enabled(self):
        connection = mock.Mock()
        version_result = mock.Mock()
        version_result.fetchone.return_value = (1,)
        journal_result = mock.Mock()
        journal_result.fetchone.return_value = ("delete",)
        connection.execute.side_effect = [version_result, journal_result]

        with self.assertRaisesRegex(RuntimeError, "WAL"):
            SQLiteDatabase(self.database_path)._initialize_schema(connection)

        self.assertEqual(
            connection.execute.call_args_list,
            [
                mock.call("PRAGMA user_version"),
                mock.call("PRAGMA journal_mode = WAL"),
            ],
        )

    def test_schema_initialization_is_atomic_when_later_ddl_fails(self):
        with sqlite3.connect(self.database_path) as connection:
            def deny_user_settings_table(action, argument, *_args):
                if action == sqlite3.SQLITE_CREATE_TABLE and argument == "user_settings":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(deny_user_settings_table)
            with self.assertRaises(sqlite3.DatabaseError):
                SQLiteDatabase(self.database_path)._initialize_schema(connection)
            connection.set_authorizer(None)

            objects = set(connection.execute(
                "SELECT type, name FROM sqlite_master WHERE name IN (?, ?)",
                ("api_keys", "idx_api_keys_username"),
            ))
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(objects, set())
        self.assertEqual(version, 0)

    def test_connection_tightens_existing_sidecar_permissions(self):
        database = SQLiteDatabase(self.database_path)

        with database.connect() as first_connection:
            first_connection.execute(
                "INSERT INTO user_settings(username, setting_key, value_json) VALUES (?, ?, ?)",
                ("admin", "CODEBUDDY_MODELS", '"model"'),
            )
            sidecars = [
                Path(f"{self.database_path}-wal"),
                Path(f"{self.database_path}-shm"),
            ]
            for sidecar in sidecars:
                os.chmod(sidecar, 0o666)

            with database.connect():
                modes = [oct(os.stat(sidecar).st_mode & 0o777) for sidecar in sidecars]

        self.assertEqual(modes, ["0o600", "0o600"])

    def test_tighten_sidecar_permissions_supports_write_only_files_without_truncating(self):
        database = SQLiteDatabase(self.database_path)
        payloads = {}
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{self.database_path}{suffix}")
            payload = f"preserved{suffix}".encode("utf-8")
            sidecar.write_bytes(payload)
            os.chmod(sidecar, 0o222)
            payloads[sidecar] = payload

        database._tighten_existing_sidecar_permissions()

        for sidecar, payload in payloads.items():
            self.assertEqual(oct(sidecar.stat().st_mode & 0o777), "0o600")
            self.assertEqual(sidecar.read_bytes(), payload)

    def test_tighten_sidecar_permissions_tolerates_file_disappearing_before_write_open(self):
        database = SQLiteDatabase(self.database_path)

        def disappearing_open(path, flags):
            access_mode = flags & (os.O_WRONLY | os.O_RDWR)
            if str(path).endswith("-wal") and access_mode == os.O_RDONLY:
                raise PermissionError("read access denied")
            raise FileNotFoundError(path)

        with mock.patch("src.sqlite_database.os.open", side_effect=disappearing_open):
            database._tighten_existing_sidecar_permissions()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is not available")
    def test_connection_rejects_symbolic_link_sidecar(self):
        database = SQLiteDatabase(self.database_path)
        with database.connect():
            pass

        target = self.temp_path / "target-wal"
        target.touch()
        os.symlink(target, Path(f"{self.database_path}-wal"))

        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            with database.connect():
                pass

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is not available")
    def test_connection_rejects_symbolic_link_rollback_journal(self):
        database = SQLiteDatabase(self.database_path)
        with database.connect():
            pass

        target = self.temp_path / "target-journal"
        target.touch()
        os.symlink(target, Path(f"{self.database_path}-journal"))

        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            with database.connect():
                pass

    def test_connection_rejects_non_regular_sidecar(self):
        database = SQLiteDatabase(self.database_path)
        with database.connect():
            pass

        Path(f"{self.database_path}-wal").mkdir()

        with self.assertRaisesRegex(RuntimeError, "regular file"):
            with database.connect():
                pass

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is not available")
    def test_connection_rejects_symbolic_link_database(self):
        target = self.temp_path / "target.sqlite3"
        target.touch()
        os.symlink(target, self.database_path)

        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            with SQLiteDatabase(self.database_path).connect():
                pass

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is not available")
    def test_connection_rejects_symbolic_link_data_directory(self):
        target = self.temp_path / "target-data"
        target.mkdir(mode=0o755)
        data_directory = self.temp_path / "data"
        os.symlink(target, data_directory)

        with self.assertRaisesRegex(RuntimeError, "symbolic link"):
            with SQLiteDatabase(data_directory / DATABASE_FILENAME).connect():
                pass

        self.assertFalse((target / DATABASE_FILENAME).exists())
        self.assertEqual(oct(os.stat(target).st_mode & 0o777), "0o755")

    def test_data_directory_descriptor_must_reference_directory(self):
        database = SQLiteDatabase(self.database_path)
        fake_stat = mock.Mock(st_mode=stat.S_IFREG | 0o600)

        with (
            mock.patch("src.sqlite_database.os.open", return_value=123),
            mock.patch("src.sqlite_database.os.fstat", return_value=fake_stat),
            mock.patch("src.sqlite_database.os.close") as close,
        ):
            with self.assertRaisesRegex(RuntimeError, "must be a directory"):
                database._prepare_data_directory()

        close.assert_called_once_with(123)

    def test_connection_rejects_non_regular_database_file(self):
        self.database_path.mkdir()

        with self.assertRaisesRegex(RuntimeError, "regular file"):
            with SQLiteDatabase(self.database_path).connect():
                pass

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is not available")
    def test_connection_rejects_fifo_database_without_blocking(self):
        os.mkfifo(self.database_path)

        with self.assertRaisesRegex(RuntimeError, "regular file"):
            with SQLiteDatabase(self.database_path).connect():
                pass

    def test_connection_rejects_unsupported_schema_version(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("PRAGMA user_version = 99")
            original_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(original_mode.lower(), "delete")

        with self.assertRaisesRegex(RuntimeError, "schema version 99"):
            with SQLiteDatabase(self.database_path).connect():
                pass

        with sqlite3.connect(self.database_path) as connection:
            current_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            current_version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(current_mode.lower(), "delete")
        self.assertEqual(current_version, 99)

    def test_connection_rolls_back_failed_transaction(self):
        database = SQLiteDatabase(self.database_path)
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO user_settings(username, setting_key, value_json) VALUES (?, ?, ?)",
                    ("admin", "CODEBUDDY_MODELS", '"temporary"'),
                )
                raise RuntimeError("rollback")

        self.assertEqual(UserSettingsStore(self.database_path).load_all(), {})

    def test_user_settings_store_returns_empty_without_creating_database(self):
        store = UserSettingsStore(self.database_path)

        self.assertEqual(store.load_all(), {})
        self.assertFalse(self.database_path.exists())

    def test_user_settings_store_upserts_typed_values(self):
        store = UserSettingsStore(self.database_path)
        store.update("admin", {"enabled": True, "count": 2, "models": "a,b"})
        store.update("admin", {"count": 3})

        self.assertEqual(
            store.load_all(),
            {"admin": {"enabled": True, "count": 3, "models": "a,b"}},
        )

    def test_user_settings_store_ignores_empty_update(self):
        store = UserSettingsStore(self.database_path)

        store.update("admin", {})

        self.assertFalse(self.database_path.exists())


class UserSettingsSchemaTests(unittest.TestCase):
    def test_boolean_setting_accepts_boolean_and_known_strings(self):
        key = "CODEBUDDY_AUTO_ROTATION_ENABLED"
        cases = [
            (True, True),
            (" yes ", True),
            ("off", False),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertIs(coerce_user_setting(key, value), expected)

    def test_boolean_setting_rejects_unknown_values(self):
        for value in (1, "maybe"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Invalid boolean"):
                    coerce_user_setting("CODEBUDDY_STRIP_MODEL_NAMESPACE", value)

    def test_rotation_count_accepts_integer_forms_and_rejects_invalid_values(self):
        self.assertEqual(coerce_user_setting("CODEBUDDY_ROTATION_COUNT", 2), 2)
        self.assertEqual(coerce_user_setting("CODEBUDDY_ROTATION_COUNT", "2"), 2)

        for value in (True, None, "2.0", 2.0, 0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    coerce_user_setting("CODEBUDDY_ROTATION_COUNT", value)

    def test_temperature_accepts_empty_integer_and_float_and_rejects_invalid_values(self):
        cases = [
            (None, ""),
            (" ", ""),
            ("1", 1),
            ("0.7", 0.7),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    coerce_user_setting("CODEBUDDY_FORCED_TEMPERATURE", value),
                    expected,
                )

        for value in ("invalid", 3):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "TEMPERATURE|number"):
                    coerce_user_setting("CODEBUDDY_FORCED_TEMPERATURE", value)

    def test_sanitize_filters_unknown_keys_and_normalizes_strings(self):
        self.assertEqual(
            sanitize_user_settings({
                "CODEBUDDY_MODELS": None,
                "CODEBUDDY_FORCED_REASONING_MODELS": " a,b ",
                "UNKNOWN": "ignored",
            }),
            {
                "CODEBUDDY_MODELS": "",
                "CODEBUDDY_FORCED_REASONING_MODELS": " a,b ",
            },
        )


if __name__ == "__main__":
    unittest.main()
