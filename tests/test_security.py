import base64
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import config
from fastapi import HTTPException
from starlette.requests import Request

from src.auth import UsersFileStore, authenticate
from src.codebuddy_token_manager import CodeBuddyTokenManager, CodeBuddyTokenManagerRegistry
from src.password_hashing import create_password_hash, verify_password


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self._original_config = config._config_cache.copy()

    def tearDown(self):
        config._config_cache = self._original_config.copy()

    def test_password_hash_verification(self):
        password_hash = create_password_hash("correct-password")

        self.assertTrue(verify_password("correct-password", password_hash))
        self.assertFalse(verify_password("wrong-password", password_hash))

    def test_users_file_store_verifies_pbkdf2_hashes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            users_file = Path(tmp_dir) / "users.txt"
            users_file.write_text(
                f"admin:{create_password_hash('secret-password')}\n",
                encoding="utf-8",
            )
            config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

            store = UsersFileStore()

            self.assertTrue(store.verify("admin", "secret-password"))
            self.assertFalse(store.verify("admin", "bad-password"))
            self.assertFalse(store.verify("missing", "secret-password"))

    def test_missing_user_still_runs_hash_verification(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            users_file = Path(tmp_dir) / "users.txt"
            users_file.write_text(
                f"admin:{create_password_hash('secret-password')}\n",
                encoding="utf-8",
            )
            config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)
            store = UsersFileStore()

            with mock.patch("src.auth.verify_password", return_value=False) as verify_mock:
                self.assertFalse(store.verify("missing", "bad-password"))

            self.assertEqual(verify_mock.call_count, 1)

    def test_authenticate_requires_users_file_identity(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            users_file = Path(tmp_dir) / "users.txt"
            users_file.write_text(
                f"admin:{create_password_hash('secret-password')}\n",
                encoding="utf-8",
            )
            config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

            user = authenticate(self._request("Bearer admin:secret-password"))
            self.assertEqual(user.username, "admin")

            with self.assertRaises(HTTPException) as context:
                authenticate(self._request("Bearer secret-password"))
            self.assertEqual(context.exception.status_code, 401)

    def test_authenticate_accepts_basic_credentials(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            users_file = Path(tmp_dir) / "users.txt"
            users_file.write_text(
                f"admin:{create_password_hash('secret-password')}\n",
                encoding="utf-8",
            )
            config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)
            token = base64.b64encode(b"admin:secret-password").decode("ascii")

            user = authenticate(self._request(f"Basic {token}"))

            self.assertEqual(user.username, "admin")

    def test_authenticate_reports_missing_users_file_as_server_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config._config_cache["CODEBUDDY_USERS_FILE"] = str(Path(tmp_dir) / "missing.txt")

            with self.assertRaises(HTTPException) as context:
                authenticate(self._request("Bearer admin:secret-password"))

            self.assertEqual(context.exception.status_code, 500)

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

    def test_unsafe_api_endpoint_falls_back_to_default(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://evil.example"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://www.codebuddy.ai"

        self.assertEqual(config.get_codebuddy_api_endpoint(), "https://www.codebuddy.ai")

    def _request(self, authorization: str) -> Request:
        return Request({
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", authorization.encode("utf-8"))],
        })


if __name__ == "__main__":
    unittest.main()
