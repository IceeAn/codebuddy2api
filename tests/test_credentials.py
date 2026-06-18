import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config

from src.auth_types import AuthenticatedUser
from src.codebuddy_token_manager import CodeBuddyTokenManager, CodeBuddyTokenManagerRegistry
from src.credential_rotation import CredentialRotationPolicy, TokenExpiry
from src.credential_store import CodeBuddyCredentialStore, build_user_credentials_dirname

from tests.helpers import ConfigIsolationMixin


def credential_record(name, expired=False):
    return {
        "file_path": f"/tmp/{name}.json",
        "data": {
            "bearer_token": f"{name}-token",
            "user_id": name,
            "expired": expired,
        },
    }


class FakeTokenExpiry:
    def is_expired(self, credential_data):
        return bool(credential_data.get("expired"))


class CredentialStoreTests(ConfigIsolationMixin, unittest.TestCase):
    def test_build_user_credentials_dirname_sanitizes_and_hashes_username(self):
        dirname = build_user_credentials_dirname(" alice@example.com ")

        self.assertTrue(dirname.startswith("alice_example.com_"))
        self.assertEqual(len(dirname.rsplit("_", 1)[1]), 12)

    def test_build_user_credentials_dirname_handles_empty_and_unsafe_username(self):
        for username in ("", "../", "   "):
            with self.subTest(username=username):
                dirname = build_user_credentials_dirname(username)
                self.assertRegex(dirname, r"^user_[a-f0-9]{12}$")

    def test_sanitize_filename_blocks_path_traversal_and_appends_json(self):
        store = CodeBuddyCredentialStore("/tmp/creds")

        self.assertEqual(store.sanitize_filename("../../outside"), "outside.json")
        self.assertEqual(store.sanitize_filename("bad:name?.json"), "bad_name_.json")

    def test_sanitize_filename_uses_timestamp_fallback_for_empty_names(self):
        store = CodeBuddyCredentialStore("/tmp/creds")

        with mock.patch("src.credential_store.time.time", return_value=123):
            self.assertEqual(store.sanitize_filename(".."), "codebuddy_token_123.json")

    def test_resolve_credential_path_rejects_escape_after_join(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = CodeBuddyCredentialStore(tmp_dir)

            with self.assertRaises(ValueError):
                store.resolve_credential_path("../outside.json")

    def test_credential_filename_cannot_escape_credentials_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            outside_path = Path(tmp_dir).parent / "outside.json"

            self.assertTrue(manager.add_credential("token-value", "user", "../../outside"))
            self.assertFalse(outside_path.exists())

            files = list(Path(tmp_dir).glob("*.json"))
            self.assertEqual(len(files), 1)
            with files[0].open("r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["bearer_token"], "token-value")
            self.assertEqual(oct(os.stat(files[0]).st_mode & 0o777), "0o600")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is not available")
    def test_credential_loader_skips_symlink_files(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            creds_dir = root / "creds"
            creds_dir.mkdir()
            outside_file = root / "outside.json"
            outside_file.write_text(
                json.dumps({"bearer_token": "outside-token"}),
                encoding="utf-8",
            )
            os.symlink(outside_file, creds_dir / "linked.json")

            manager = CodeBuddyTokenManager(creds_dir=str(creds_dir))

            self.assertEqual(manager.credentials, [])


class TokenExpiryTests(unittest.TestCase):
    def test_missing_expiry_fields_are_not_expired(self):
        self.assertFalse(TokenExpiry().is_expired({"bearer_token": "token"}))

    def test_expiry_uses_five_minute_buffer_boundary(self):
        expiry = TokenExpiry()

        with mock.patch("src.credential_rotation.time.time", return_value=1000):
            self.assertFalse(expiry.is_expired({"created_at": 100, "expires_in": 1201}))
            self.assertTrue(expiry.is_expired({"created_at": 100, "expires_in": 1200}))

    def test_malformed_expiry_data_is_treated_as_not_expired(self):
        self.assertFalse(TokenExpiry().is_expired({"created_at": "bad", "expires_in": 1}))


class CredentialRotationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = CredentialRotationPolicy(FakeTokenExpiry())

    def test_select_returns_none_when_no_credentials(self):
        selection = self.policy.select([], 0, 0, None, True, 1)

        self.assertIsNone(selection.credential_record)
        self.assertEqual(selection.current_index, 0)

    def test_select_skips_expired_credentials_and_resets_invalid_current(self):
        credentials = [
            credential_record("expired", expired=True),
            credential_record("valid"),
        ]

        selection = self.policy.select(credentials, 0, 5, None, True, 2)

        self.assertEqual(selection.credential_record, credentials[1])
        self.assertEqual(selection.current_index, 1)
        self.assertEqual(selection.usage_count, 1)

    def test_select_returns_none_when_all_credentials_expired(self):
        selection = self.policy.select([credential_record("expired", expired=True)], 0, 0, None, True, 1)

        self.assertIsNone(selection.credential_record)

    def test_manual_selection_wins_when_valid(self):
        credentials = [credential_record("a"), credential_record("b")]

        selection = self.policy.select(credentials, 0, 0, 1, True, 1)

        self.assertEqual(selection.credential_record, credentials[1])
        self.assertEqual(selection.manual_selected_index, 1)
        self.assertEqual(selection.usage_count, 0)

    def test_expired_manual_selection_falls_back_to_rotation(self):
        credentials = [credential_record("a"), credential_record("b", expired=True)]

        selection = self.policy.select(credentials, 0, 0, 1, True, 1)

        self.assertEqual(selection.credential_record, credentials[0])
        self.assertIsNone(selection.manual_selected_index)

    def test_rotation_occurs_when_usage_count_reaches_threshold(self):
        credentials = [credential_record("a"), credential_record("b")]

        selection = self.policy.select(credentials, 0, 2, None, True, 2)

        self.assertEqual(selection.credential_record, credentials[1])
        self.assertEqual(selection.current_index, 1)
        self.assertEqual(selection.usage_count, 1)

    def test_rotation_disabled_keeps_current_credential_without_increment(self):
        credentials = [credential_record("a"), credential_record("b")]

        selection = self.policy.select(credentials, 0, 2, None, False, 2)

        self.assertEqual(selection.credential_record, credentials[0])
        self.assertEqual(selection.usage_count, 2)

    def test_zero_rotation_count_keeps_current_credential(self):
        credentials = [credential_record("a"), credential_record("b")]

        selection = self.policy.select(credentials, 0, 2, None, True, 0)

        self.assertEqual(selection.credential_record, credentials[0])
        self.assertEqual(selection.usage_count, 2)


class TokenManagerTests(ConfigIsolationMixin, unittest.TestCase):
    def test_token_manager_preserves_current_credential_after_deleting_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config._config_cache["CODEBUDDY_ROTATION_COUNT"] = 0
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            manager.current_index = 1

            self.assertTrue(manager.delete_credential_by_index(0))

            self.assertEqual(manager.current_index, 0)
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_preserves_current_credential_after_adding_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config._config_cache["CODEBUDDY_ROTATION_COUNT"] = 0
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            manager.current_index = 0

            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))

            self.assertEqual(manager.current_index, 1)
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_preserves_manual_credential_after_deleting_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            self.assertTrue(manager.set_manual_credential(1))

            self.assertTrue(manager.delete_credential_by_index(0))

            self.assertEqual(manager.manual_selected_index, 0)
            self.assertEqual(manager.current_index, 0)
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_restores_manual_credential_by_filename_after_prior_file_removed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            self.assertTrue(manager.set_manual_credential(1))
            os.remove(Path(tmp_dir) / "a.json")

            reloaded_manager = CodeBuddyTokenManager(creds_dir=tmp_dir)

            self.assertEqual(reloaded_manager.manual_selected_index, 0)
            self.assertEqual(reloaded_manager.current_index, 0)
            self.assertEqual(reloaded_manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_does_not_restore_missing_current_filename_by_legacy_index(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            manager.current_index = 1
            manager.save_state()
            os.remove(Path(tmp_dir) / "b.json")

            reloaded_manager = CodeBuddyTokenManager(creds_dir=tmp_dir)

            self.assertEqual(reloaded_manager.current_index, 0)
            self.assertEqual(reloaded_manager.get_next_credential()["bearer_token"], "a-token")

    def test_token_manager_rejects_invalid_manual_selection_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))

            self.assertFalse(manager.set_manual_credential(-1))
            self.assertFalse(manager.set_manual_credential(1))

    def test_token_manager_current_info_reports_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)

            self.assertEqual(manager.get_current_credential_info(), {"status": "no_credentials"})

    def test_token_manager_registry_isolates_users(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config._config_cache["CODEBUDDY_CREDS_DIR"] = tmp_dir
            registry = CodeBuddyTokenManagerRegistry()

            alice_manager = registry.for_username("alice@example.com")
            bob_manager = registry.for_username("bob@example.com")
            self.assertNotEqual(alice_manager.creds_dir, bob_manager.creds_dir)

            self.assertTrue(alice_manager.add_credential("alice-token", "alice-upstream"))
            self.assertTrue(bob_manager.add_credential("bob-token", "bob-upstream"))

            self.assertEqual(
                [cred["bearer_token"] for cred in alice_manager.get_all_credentials()],
                ["alice-token"],
            )
            self.assertEqual(
                [cred["bearer_token"] for cred in bob_manager.get_all_credentials()],
                ["bob-token"],
            )

    def test_get_token_manager_for_user_uses_username_identity(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config._config_cache["CODEBUDDY_CREDS_DIR"] = tmp_dir
            registry = CodeBuddyTokenManagerRegistry()

            first = registry.for_user(AuthenticatedUser(username="alice", source="session_cookie"))
            second = registry.for_username("alice")

            self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
