import base64
import hashlib
import os
import secrets
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import config

from src.api_key_store import API_KEY_SECRET_BYTES, ApiKeyStore
from tests.helpers import TempConfigMixin, configure_users_file


class ApiKeyStoreTests(TempConfigMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config._config_cache["CODEBUDDY_DATA_DIR"] = self._temp_dir.name
        self.database_path = Path(self._temp_dir.name) / "codebuddy2api.sqlite3"

    def test_create_key_sanitizes_name_and_only_persists_sha256_digest(self):
        store = ApiKeyStore()

        blank = store.create_key("admin", " ")
        long = store.create_key("admin", "x" * 100)

        self.assertEqual(blank["name"], "API Key")
        self.assertEqual(long["name"], "x" * 80)
        self.assertTrue(blank["api_key"].startswith("sk-"))
        self.assertEqual(API_KEY_SECRET_BYTES, 40)
        self.assertEqual(len(blank["api_key"]), 57)
        self.assertEqual(blank["preview"], f"{blank['api_key'][:10]}...{blank['api_key'][-4:]}")

        self.assertEqual(oct(os.stat(self.database_path).st_mode & 0o777), "0o600")
        with sqlite3.connect(self.database_path) as connection:
            rows = connection.execute("SELECT key_digest, preview FROM api_keys").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotIn(blank["api_key"], str(rows))
        self.assertEqual(rows[0][0], hashlib.sha256(blank["api_key"].encode("utf-8")).digest())
        self.assertIn(blank["preview"], {row[1] for row in rows})

    def test_list_keys_excludes_secret_material_and_filters_by_username(self):
        store = ApiKeyStore()
        admin_key = store.create_key("admin", "admin")
        store.create_key("alice", "alice")

        listed = store.list_keys("admin")

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], admin_key["id"])
        self.assertNotIn("api_key", listed[0])
        self.assertNotIn("key_digest", listed[0])

    def test_delete_key_is_scoped_to_username(self):
        store = ApiKeyStore()
        admin_key = store.create_key("admin", "admin")
        alice_key = store.create_key("alice", "alice")

        self.assertFalse(store.delete_key("admin", alice_key["id"]))
        self.assertTrue(store.delete_key("admin", admin_key["id"]))
        self.assertEqual(store.list_keys("admin"), [])
        self.assertEqual(len(store.list_keys("alice")), 1)

    def test_verify_rejects_malformed_keys_without_digest_or_database_access(self):
        store = ApiKeyStore()

        with (
            mock.patch("src.api_key_store.hashlib.sha256", wraps=hashlib.sha256) as digest_mock,
            mock.patch.object(store, "_database") as database_mock,
        ):
            self.assertIsNone(store.verify("not-sk"))
            self.assertIsNone(store.verify("sk-missing"))

        digest_mock.assert_not_called()
        database_mock.assert_not_called()

    def test_api_key_decoder_rejects_noncanonical_and_invalid_encodings(self):
        valid_secret = secrets.token_urlsafe(40)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        last_index = alphabet.index(valid_secret[-1])
        noncanonical_last = alphabet[(last_index & ~15) | 1]
        invalid_keys = (
            None,
            "sk-",
            "sk-value=",
            f"sk-{'A' * 53}=",
            "sk-AAAAA",
            f"sk-{'é' * 54}",
            f"sk-{'*' * 54}",
            "sk-YQ",
            f"sk-{valid_secret[:-1]}{noncanonical_last}",
        )

        for api_key in invalid_keys:
            with self.subTest(api_key=api_key):
                self.assertIsNone(ApiKeyStore._decode_api_key(api_key))

    def test_api_key_decoder_checks_exact_encoded_length_before_decoding(self):
        valid_encoded = secrets.token_urlsafe(API_KEY_SECRET_BYTES)
        expected_secret = base64.urlsafe_b64decode(valid_encoded + "==")

        with mock.patch(
            "src.api_key_store.base64.b64decode",
            wraps=base64.b64decode,
        ) as decode_mock:
            self.assertIsNone(ApiKeyStore._decode_api_key(f"sk-{valid_encoded[:-1]}"))
            self.assertEqual(
                ApiKeyStore._decode_api_key(f"sk-{valid_encoded}"), expected_secret
            )
            self.assertIsNone(ApiKeyStore._decode_api_key(f"sk-{valid_encoded}A"))

        self.assertEqual(decode_mock.call_count, 1)

    def test_api_key_decoder_rejects_oversized_input_before_decoding(self):
        oversized_api_key = f"sk-{'A' * 1_000_000}"

        with mock.patch(
            "src.api_key_store.base64.b64decode",
            wraps=base64.b64decode,
        ) as decode_mock:
            self.assertIsNone(ApiKeyStore._decode_api_key(oversized_api_key))

        self.assertEqual(decode_mock.call_count, 0)

    def test_verify_well_formed_key_does_not_create_missing_database(self):
        api_key = f"sk-{secrets.token_urlsafe(40)}"

        self.assertIsNone(ApiKeyStore().verify(api_key))
        self.assertFalse(self.database_path.exists())

    def test_verify_uses_one_sha256_and_indexed_lookup_for_unknown_key(self):
        store = ApiKeyStore()
        for index in range(8):
            store.create_key("admin", f"existing-{index}")
        unknown_key = f"sk-{secrets.token_urlsafe(40)}"

        with mock.patch("src.api_key_store.hashlib.sha256", wraps=hashlib.sha256) as digest_mock:
            self.assertIsNone(store.verify(unknown_key))

        digest_mock.assert_called_once()

    def test_verify_uses_single_database_connection(self):
        store = ApiKeyStore()
        created = store.create_key("admin", "client")
        database = store._database()

        with (
            mock.patch.object(store, "_database", return_value=database),
            mock.patch.object(database, "connect", wraps=database.connect) as connect_mock,
            mock.patch("src.api_key_store.users_store.has_username", return_value=True),
        ):
            self.assertIsNotNone(store.verify(created["api_key"]))

        connect_mock.assert_called_once_with()

    def test_verify_rejects_key_when_owner_user_no_longer_exists(self):
        configure_users_file(self.temp_path, {"admin": "secret-password"})
        store = ApiKeyStore()
        created = store.create_key("ghost", "client")

        self.assertIsNone(store.verify(created["api_key"]))

    def test_verify_records_last_used_at_as_minute_and_writes_once_per_minute(self):
        configure_users_file(self.temp_path, {"admin": "secret-password"})
        store = ApiKeyStore()
        created = store.create_key("admin", "client")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("CREATE TABLE api_key_update_audit(marker INTEGER)")
            connection.execute(
                """
                CREATE TRIGGER audit_api_key_update
                AFTER UPDATE OF last_used_at ON api_keys
                BEGIN
                    INSERT INTO api_key_update_audit(marker) VALUES (1);
                END
                """
            )

        with mock.patch("src.api_key_store.time.time", return_value=125):
            user = store.verify(created["api_key"])
            self.assertIsNotNone(store.verify(created["api_key"]))
        with mock.patch("src.api_key_store.time.time", return_value=179):
            self.assertIsNotNone(store.verify(created["api_key"]))
        with mock.patch("src.api_key_store.time.time", return_value=180):
            self.assertIsNotNone(store.verify(created["api_key"]))
        listed = store.list_keys("admin")
        with sqlite3.connect(self.database_path) as connection:
            update_count = connection.execute(
                "SELECT COUNT(*) FROM api_key_update_audit"
            ).fetchone()[0]

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "api_key")
        self.assertEqual(listed[0]["last_used_at"], 180)
        self.assertEqual(update_count, 2)

    def test_concurrent_verification_only_writes_last_used_once_per_minute(self):
        store = ApiKeyStore()
        created = store.create_key("admin", "client")
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("CREATE TABLE api_key_update_audit(marker INTEGER)")
            connection.execute(
                """
                CREATE TRIGGER audit_api_key_update
                AFTER UPDATE OF last_used_at ON api_keys
                BEGIN
                    INSERT INTO api_key_update_audit(marker) VALUES (1);
                END
                """
            )

        with (
            mock.patch("src.api_key_store.users_store.has_username", return_value=True),
            mock.patch("src.api_key_store.time.time", return_value=125),
            ThreadPoolExecutor(max_workers=4) as executor,
        ):
            users = list(executor.map(lambda _index: store.verify(created["api_key"]), range(8)))

        self.assertTrue(all(user is not None for user in users))
        with sqlite3.connect(self.database_path) as connection:
            update_count = connection.execute(
                "SELECT COUNT(*) FROM api_key_update_audit"
            ).fetchone()[0]
        self.assertEqual(update_count, 1)

    def test_verify_rejects_key_deleted_during_owner_check(self):
        store = ApiKeyStore()
        created = store.create_key("admin", "client")
        owner_check_started = threading.Event()
        continue_owner_check = threading.Event()
        verify_result = []
        verify_errors = []

        def delayed_owner_check(_username):
            owner_check_started.set()
            if not continue_owner_check.wait(timeout=2):
                raise TimeoutError("owner check was not released")
            return True

        def run_verify():
            try:
                verify_result.append(store.verify(created["api_key"]))
            except Exception as error:
                verify_errors.append(error)

        with mock.patch(
            "src.api_key_store.users_store.has_username", side_effect=delayed_owner_check
        ):
            verify_thread = threading.Thread(target=run_verify)
            verify_thread.start()
            self.assertTrue(owner_check_started.wait(timeout=2))
            self.assertTrue(store.delete_key("admin", created["id"]))
            continue_owner_check.set()
            verify_thread.join(timeout=2)

        self.assertFalse(verify_thread.is_alive())
        self.assertEqual(verify_errors, [])
        self.assertEqual(verify_result, [None])

    def test_independent_instances_share_database_state(self):
        created = ApiKeyStore().create_key("admin", "client")

        listed = ApiKeyStore().list_keys("admin")

        self.assertEqual([item["id"] for item in listed], [created["id"]])

    def test_list_and_delete_return_empty_for_missing_database(self):
        store = ApiKeyStore()

        self.assertEqual(store.list_keys("admin"), [])
        self.assertFalse(store.delete_key("admin", "missing"))

    def test_concurrent_creates_do_not_lose_records(self):
        store = ApiKeyStore()
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda index: store.create_key("admin", f"key-{index}"), range(8)))

        self.assertEqual(len(store.list_keys("admin")), 8)

    def test_resolve_database_path_uses_shared_config_path(self):
        store = ApiKeyStore()
        expected_path = self.temp_path / "shared-data" / "codebuddy2api.sqlite3"
        with mock.patch(
            "src.api_key_store.get_database_path",
            return_value=expected_path,
        ):
            path = store._resolve_database_path()

        self.assertEqual(path, expected_path)


if __name__ == "__main__":
    unittest.main()
