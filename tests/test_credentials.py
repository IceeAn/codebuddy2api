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

    def test_save_credential_for_new_file_rejects_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = CodeBuddyCredentialStore(tmp_dir)
            store.save_credential({"bearer_token": "first"}, "codebuddy_token_2.json")

            with self.assertRaises(FileExistsError):
                store.save_credential({"bearer_token": "second"}, "codebuddy_token_2.json", create_new=True)


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
        selection = self.policy.select([], 0, 0, True, 1)

        self.assertIsNone(selection.credential_record)
        self.assertEqual(selection.current_index, 0)

    def test_select_skips_expired_credentials_and_resets_invalid_current(self):
        credentials = [
            credential_record("expired", expired=True),
            credential_record("valid"),
        ]

        selection = self.policy.select(credentials, 0, 5, True, 2)

        self.assertEqual(selection.credential_record, credentials[1])
        self.assertEqual(selection.current_index, 1)
        self.assertEqual(selection.usage_count, 1)

    def test_select_returns_none_when_all_credentials_expired(self):
        selection = self.policy.select([credential_record("expired", expired=True)], 0, 0, True, 1)

        self.assertIsNone(selection.credential_record)

    def test_rotation_occurs_when_usage_count_reaches_threshold(self):
        credentials = [credential_record("a"), credential_record("b")]

        selection = self.policy.select(credentials, 0, 2, True, 2)

        self.assertEqual(selection.credential_record, credentials[1])
        self.assertEqual(selection.current_index, 1)
        self.assertEqual(selection.usage_count, 1)

    def test_rotation_disabled_keeps_current_credential_without_increment(self):
        credentials = [credential_record("a"), credential_record("b")]

        selection = self.policy.select(credentials, 0, 2, False, 2)

        self.assertEqual(selection.credential_record, credentials[0])
        self.assertEqual(selection.usage_count, 2)


class TokenManagerTests(ConfigIsolationMixin, unittest.TestCase):
    def test_add_credential_uses_non_colliding_numeric_filename_after_gap(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("first-token", "first-user", "codebuddy_token_1.json"))
            self.assertTrue(manager.add_credential("second-token", "second-user", "codebuddy_token_2.json"))
            os.remove(Path(tmp_dir) / "codebuddy_token_1.json")
            manager.load_all_tokens()

            self.assertTrue(manager.add_credential("third-token", "third-user"))

            files = sorted(path.name for path in Path(tmp_dir).glob("codebuddy_token_*.json"))
            self.assertEqual(files, ["codebuddy_token_2.json", "codebuddy_token_3.json"])
            with (Path(tmp_dir) / "codebuddy_token_2.json").open("r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["bearer_token"], "second-token")
            with (Path(tmp_dir) / "codebuddy_token_3.json").open("r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["bearer_token"], "third-token")

    def test_add_credential_with_data_generates_unique_timestamp_filename(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)

            with mock.patch("src.codebuddy_token_manager.time.time", return_value=123):
                self.assertTrue(manager.add_credential_with_data({"bearer_token": "first", "user_id": "same"}))
                self.assertTrue(manager.add_credential_with_data({"bearer_token": "second", "user_id": "same"}))

            files = sorted(path.name for path in Path(tmp_dir).glob("codebuddy_same_123*.json"))
            self.assertEqual(files, ["codebuddy_same_123.json", "codebuddy_same_123_1.json"])

    def test_add_credential_with_data_retries_atomic_filename_collision(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            original_save = manager.store.save_credential
            collided = False

            def collide_once(credential_data, filename, indent=4, create_new=False):
                nonlocal collided
                if not collided:
                    collided = True
                    original_save({"bearer_token": "other"}, filename, indent, create_new)
                    raise FileExistsError(filename)
                return original_save(credential_data, filename, indent, create_new)

            with mock.patch.object(manager.store, "save_credential", side_effect=collide_once):
                self.assertTrue(
                    manager.add_credential_with_data(
                        {"bearer_token": "requested", "user_id": "same", "created_at": 123}
                    )
                )

            files = sorted(path.name for path in Path(tmp_dir).glob("codebuddy_same_123*.json"))
            self.assertEqual(files, ["codebuddy_same_123.json", "codebuddy_same_123_1.json"])
            with (Path(tmp_dir) / "codebuddy_same_123_1.json").open("r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["bearer_token"], "requested")

    def test_token_manager_preserves_current_credential_after_deleting_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            manager.disable_auto_rotation()
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            manager.current_index = 1

            self.assertTrue(manager.delete_credential_by_index(0))

            self.assertEqual(manager.current_index, 0)
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_preserves_current_credential_after_adding_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            manager.disable_auto_rotation()
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            manager.current_index = 0

            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))

            self.assertEqual(manager.current_index, 1)
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_preserves_fixed_credential_after_deleting_prior_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            self.assertTrue(manager.set_current_credential(1))

            self.assertTrue(manager.delete_credential_by_index(0))

            self.assertEqual(manager.current_index, 0)
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_restores_current_credential_by_filename_after_prior_file_removed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))
            self.assertTrue(manager.add_credential("c-token", "c-user", "c"))
            manager.current_index = 1
            manager.save_state()
            os.remove(Path(tmp_dir) / "a.json")

            reloaded_manager = CodeBuddyTokenManager(creds_dir=tmp_dir)

            self.assertEqual(reloaded_manager.current_index, 0)
            self.assertEqual(reloaded_manager.get_next_credential()["bearer_token"], "b-token")

    def test_token_manager_does_not_restore_missing_current_filename_by_index(self):
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

    def test_token_manager_rejects_invalid_current_credential_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))

            self.assertFalse(manager.set_current_credential(-1))
            self.assertFalse(manager.set_current_credential(1))

    def test_token_manager_current_info_reports_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)

            self.assertEqual(
                manager.get_current_credential_info(),
                {
                    "status": "no_credentials",
                    "rotation_count": 1,
                    "auto_rotation_enabled": True,
                },
            )

    def test_token_manager_exposes_stable_credential_ids(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))

            credentials = manager.get_credentials_info()
            first_id = credentials[0]["credential_id"]
            second_id = credentials[1]["credential_id"]

            self.assertNotEqual(first_id, second_id)
            self.assertEqual(manager.get_credential_by_id(first_id)["bearer_token"], "a-token")
            self.assertTrue(manager.set_current_credential_by_id(second_id))
            self.assertEqual(manager.get_current_credential_info()["credential_id"], second_id)
            self.assertEqual(manager.get_current_credential_info()["status"], "auto_rotation_disabled")
            self.assertTrue(manager.delete_credential_by_id(first_id))
            self.assertIsNone(manager.get_credential_by_id(first_id))

    def test_selecting_credential_disables_rotation_and_enabling_starts_from_current(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = CodeBuddyTokenManager(creds_dir=tmp_dir)
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))

            self.assertTrue(manager.set_current_credential(1))
            self.assertFalse(manager._is_auto_rotation_enabled())
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")

            manager.enable_auto_rotation()

            self.assertTrue(manager._is_auto_rotation_enabled())
            self.assertEqual(manager.get_next_credential()["bearer_token"], "b-token")
            self.assertEqual(manager.get_next_credential()["bearer_token"], "a-token")

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

    def test_token_manager_uses_user_scoped_rotation_settings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config._config_cache["CODEBUDDY_CREDS_DIR"] = tmp_dir
            config.update_settings(
                {
                    "CODEBUDDY_AUTO_ROTATION_ENABLED": False,
                    "CODEBUDDY_ROTATION_COUNT": 1,
                },
                username="alice",
            )
            registry = CodeBuddyTokenManagerRegistry()
            manager = registry.for_user(AuthenticatedUser(username="alice", source="session_cookie"))
            self.assertTrue(manager.add_credential("a-token", "a-user", "a"))
            self.assertTrue(manager.add_credential("b-token", "b-user", "b"))

            first = manager.get_next_credential()
            second = manager.get_next_credential()
            current = manager.get_current_credential_info()

            self.assertEqual(first["bearer_token"], "a-token")
            self.assertEqual(second["bearer_token"], "a-token")
            self.assertEqual(current["status"], "auto_rotation_disabled")
            self.assertIs(current["auto_rotation_enabled"], False)

    def test_token_manager_toggle_auto_rotation_updates_user_setting(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config._config_cache["CODEBUDDY_CREDS_DIR"] = tmp_dir
            registry = CodeBuddyTokenManagerRegistry()
            manager = registry.for_user(AuthenticatedUser(username="alice", source="session_cookie"))

            self.assertTrue(config.get_auto_rotation_enabled("alice"))
            self.assertFalse(manager.toggle_auto_rotation())
            self.assertFalse(config.get_auto_rotation_enabled("alice"))


if __name__ == "__main__":
    unittest.main()
