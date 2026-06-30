import base64
import unittest
from unittest import mock

import config
from fastapi import HTTPException, Response

from src.auth_router import (
    authenticate,
    login as service_login,
    logout as service_logout,
    require_session_user,
)
from src.auth_types import (
    SESSION_COOKIE_NAME,
    LoginRequest,
)
from src.api_key_store import api_key_store
from src.password_hashing import create_password_hash, verify_password
from src.users_store import UsersFileStore

from tests.helpers import TempConfigMixin, configure_users_file, make_request


class PasswordHashingTests(unittest.TestCase):
    def test_password_hash_verification(self):
        password_hash = create_password_hash("correct-password")

        self.assertTrue(verify_password("correct-password", password_hash))
        self.assertFalse(verify_password("wrong-password", password_hash))

    def test_verify_password_accepts_minimum_iteration_boundary(self):
        password_hash = create_password_hash("secret", iterations=100000)

        self.assertTrue(verify_password("secret", password_hash))

    def test_verify_password_rejects_low_iteration_hash(self):
        password_hash = create_password_hash("secret", iterations=99999)

        self.assertFalse(verify_password("secret", password_hash))

    def test_verify_password_rejects_invalid_hash_equivalence_classes(self):
        invalid_hashes = [
            "",
            "not-a-hash",
            "sha256$390000$salt$digest",
            "pbkdf2_sha256$not-int$salt$digest",
            "pbkdf2_sha256$390000$@@@$digest",
        ]

        for password_hash in invalid_hashes:
            with self.subTest(password_hash=password_hash):
                self.assertFalse(verify_password("secret", password_hash))


class UsersFileStoreTests(TempConfigMixin, unittest.TestCase):
    def test_users_file_store_verifies_pbkdf2_hashes(self):
        configure_users_file(self.temp_path)

        store = UsersFileStore()

        self.assertTrue(store.verify("admin", "secret-password"))
        self.assertFalse(store.verify("admin", "bad-password"))
        self.assertFalse(store.verify("missing", "secret-password"))

    def test_missing_user_still_runs_hash_verification(self):
        configure_users_file(self.temp_path)
        store = UsersFileStore()

        with mock.patch("src.users_store.verify_password", return_value=False) as verify_mock:
            self.assertFalse(store.verify("missing", "bad-password"))

        self.assertEqual(verify_mock.call_count, 1)

    def test_users_file_ignores_blank_comments_and_malformed_lines(self):
        users_file = self.temp_path / "users.txt"
        users_file.write_text(
            "\n"
            "# comment\n"
            "missing-separator\n"
            ":missing-username\n"
            "missing-hash:\n"
            f" admin : {create_password_hash('secret-password')} \n",
            encoding="utf-8",
        )
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

        store = UsersFileStore()

        self.assertTrue(store.verify("admin", "secret-password"))
        self.assertFalse(store.has_username("missing-separator"))

    def test_missing_users_file_reports_no_configured_users(self):
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(self.temp_path / "missing.txt")

        store = UsersFileStore()

        self.assertFalse(store.has_users_file())
        self.assertFalse(store.verify("admin", "secret-password"))


class AuthDependencyTests(TempConfigMixin, unittest.TestCase):
    def test_authenticate_requires_users_file_identity(self):
        configure_users_file(self.temp_path)

        with self.assertRaises(HTTPException) as context:
            authenticate(make_request(authorization="Bearer admin:secret-password"))

        self.assertEqual(context.exception.status_code, 401)

    def test_authenticate_rejects_basic_credentials(self):
        configure_users_file(self.temp_path)
        token = base64.b64encode(b"admin:secret-password").decode("ascii")

        with self.assertRaises(HTTPException) as context:
            authenticate(make_request(authorization=f"Basic {token}"))

        self.assertEqual(context.exception.status_code, 401)

    def test_authenticate_rejects_empty_bearer_token(self):
        configure_users_file(self.temp_path)

        with self.assertRaises(HTTPException) as context:
            authenticate(make_request(authorization="Bearer "))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.headers, {"WWW-Authenticate": "Bearer"})

    def test_authenticate_accepts_generated_api_key(self):
        configure_users_file(self.temp_path)
        config._config_cache["CODEBUDDY_CREDS_DIR"] = self._temp_dir.name

        key_data = api_key_store.create_key("admin", "test")
        user = authenticate(make_request(authorization=f"Bearer {key_data['api_key']}"))

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "api_key")

    def test_authenticate_reports_missing_users_file_as_server_error(self):
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(self.temp_path / "missing.txt")

        with self.assertRaises(HTTPException) as context:
            authenticate(make_request(authorization="Bearer admin:secret-password"))

        self.assertEqual(context.exception.status_code, 500)


class AuthSessionTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        config._config_cache["CODEBUDDY_CREDS_DIR"] = self._temp_dir.name

    async def _login_and_get_cookie(self, request=None) -> str:
        response = Response()
        result = await service_login(
            request or make_request(),
            response,
            LoginRequest(username="admin", password="secret-password"),
        )

        self.assertTrue(result["authenticated"])
        set_cookie = response.headers["set-cookie"]
        self.assertIn(SESSION_COOKIE_NAME, set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        return set_cookie.split(";", 1)[0]

    async def test_login_cookie_authenticates_followup_requests(self):
        cookie = await self._login_and_get_cookie()

        user = authenticate(make_request(cookie=cookie))

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "session_cookie")

    async def test_login_sets_secure_cookie_for_forwarded_https(self):
        response = Response()

        await service_login(
            make_request(extra_headers={"X-Forwarded-Proto": "https"}),
            response,
            LoginRequest(username="admin", password="secret-password"),
        )

        self.assertIn("Secure", response.headers["set-cookie"])

    async def test_login_rejects_blank_username_and_password(self):
        with self.assertRaises(HTTPException) as context:
            await service_login(make_request(), Response(), LoginRequest(username=" ", password=""))

        self.assertEqual(context.exception.status_code, 401)

    async def test_logout_invalidates_session_cookie(self):
        cookie = await self._login_and_get_cookie()
        response = Response()

        result = await service_logout(make_request(cookie=cookie), response)

        self.assertFalse(result["authenticated"])
        with self.assertRaises(HTTPException) as context:
            authenticate(make_request(cookie=cookie))
        self.assertEqual(context.exception.status_code, 401)

    async def test_api_key_cannot_manage_api_keys(self):
        cookie = await self._login_and_get_cookie()
        session_user = authenticate(make_request(cookie=cookie))
        created = api_key_store.create_key(session_user.username, "client")

        with self.assertRaises(HTTPException):
            require_session_user(make_request(authorization=f"Bearer {created['api_key']}"))


if __name__ == "__main__":
    unittest.main()
