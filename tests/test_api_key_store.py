import json
import os
import unittest
from pathlib import Path
from unittest import mock

import config

from src.api_key_store import ApiKeyStore

from tests.helpers import TempConfigMixin, configure_users_file


class ApiKeyStoreTests(TempConfigMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config._config_cache["CODEBUDDY_CREDS_DIR"] = self._temp_dir.name

    def test_create_key_sanitizes_blank_and_long_names(self):
        store = ApiKeyStore()

        blank = store.create_key("admin", " ")
        long = store.create_key("admin", "x" * 100)

        self.assertEqual(blank["name"], "API Key")
        self.assertEqual(long["name"], "x" * 80)
        self.assertTrue(blank["api_key"].startswith("sk-"))
        self.assertEqual(blank["preview"], f"{blank['api_key'][:10]}...{blank['api_key'][-4:]}")

    def test_create_key_persists_hash_only_with_private_permissions(self):
        store = ApiKeyStore()

        created = store.create_key("admin", "client")
        api_keys_file = Path(self._temp_dir.name) / "api_keys.json"

        self.assertEqual(oct(os.stat(api_keys_file).st_mode & 0o777), "0o600")
        with api_keys_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn(created["api_key"], json.dumps(data))
        self.assertEqual(data["keys"][0]["preview"], created["preview"])

    def test_list_keys_excludes_secret_material_and_filters_by_username(self):
        store = ApiKeyStore()
        admin_key = store.create_key("admin", "admin")
        store.create_key("alice", "alice")

        listed = store.list_keys("admin")

        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], admin_key["id"])
        self.assertNotIn("api_key", listed[0])
        self.assertNotIn("key_hash", listed[0])

    def test_delete_key_is_scoped_to_username(self):
        store = ApiKeyStore()
        admin_key = store.create_key("admin", "admin")
        alice_key = store.create_key("alice", "alice")

        self.assertFalse(store.delete_key("admin", alice_key["id"]))
        self.assertTrue(store.delete_key("admin", admin_key["id"]))
        self.assertEqual(store.list_keys("admin"), [])
        self.assertEqual(len(store.list_keys("alice")), 1)

    def test_verify_rejects_non_prefixed_key_and_runs_dummy_hash(self):
        store = ApiKeyStore()

        with mock.patch("src.api_key_store.verify_password", return_value=False) as verify_mock:
            self.assertIsNone(store.verify("not-sk"))

        self.assertEqual(verify_mock.call_count, 1)

    def test_verify_rejects_unknown_prefixed_key_and_runs_dummy_hash(self):
        store = ApiKeyStore()

        with mock.patch("src.api_key_store.verify_password", return_value=False) as verify_mock:
            self.assertIsNone(store.verify("sk-missing"))

        self.assertGreaterEqual(verify_mock.call_count, 1)

    def test_verify_rejects_key_when_owner_user_no_longer_exists(self):
        configure_users_file(self.temp_path, {"admin": "secret-password"})
        store = ApiKeyStore()
        created = store.create_key("ghost", "client")

        self.assertIsNone(store.verify(created["api_key"]))

    def test_verify_updates_last_used_for_valid_key(self):
        configure_users_file(self.temp_path, {"admin": "secret-password"})
        store = ApiKeyStore()
        created = store.create_key("admin", "client")

        user = store.verify(created["api_key"])
        listed = store.list_keys("admin")

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "api_key")
        self.assertIsInstance(listed[0]["last_used_at"], int)


if __name__ == "__main__":
    unittest.main()
