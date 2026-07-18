import base64
import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import config
import httpx
from fastapi import HTTPException, Response

from src.auth_router import (
    get_session,
    login as service_login,
    logout as service_logout,
    require_api_key_user,
    require_session_user,
)
from src.auth_types import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE_NAME,
    LoginRequest,
)
from src.api_key_store import api_key_store
from src.password_hashing import (
    PBKDF2_MAX_ITERATIONS,
    PBKDF2_ITERATIONS,
    PBKDF2_MIN_ITERATIONS,
    _decode_unpadded_base64,
    create_password_hash,
    is_supported_password_hash,
    verify_password,
)
from src.login_security import LoginAttemptGuard
from src.users_store import (
    UsersFileConfigurationError,
    UsersFileStore,
    validate_configured_users_file,
)

from tests.helpers import TempConfigMixin, configure_users_file, make_request
from web import app


class PasswordHashingTests(unittest.TestCase):
    def test_default_and_dummy_hash_use_600000_iterations(self):
        password_hash = create_password_hash("secret")

        self.assertEqual(PBKDF2_ITERATIONS, 600000)
        self.assertEqual(password_hash.split("$")[1], "600000")
        self.assertEqual(DUMMY_PASSWORD_HASH.split("$")[1], "600000")

    def test_password_hash_verification(self):
        password_hash = create_password_hash("correct-password")

        self.assertTrue(verify_password("correct-password", password_hash))
        self.assertFalse(verify_password("wrong-password", password_hash))

    def test_verify_password_accepts_minimum_iteration_boundary(self):
        password_hash = create_password_hash("secret", iterations=600000)

        self.assertEqual(PBKDF2_MIN_ITERATIONS, 600000)
        self.assertTrue(verify_password("secret", password_hash))

    def test_verify_password_accepts_maximum_iteration_boundary(self):
        password_hash = create_password_hash("secret", iterations=PBKDF2_MAX_ITERATIONS)

        self.assertTrue(verify_password("secret", password_hash))

    def test_verify_password_rejects_low_iteration_hash(self):
        password_hash = create_password_hash("secret")

        for iterations in ("599999", "100000"):
            with self.subTest(iterations=iterations):
                malformed_hash = password_hash.replace("$600000$", f"${iterations}$")
                self.assertFalse(verify_password("secret", malformed_hash))

    def test_verify_password_rejects_excessive_and_noncanonical_iterations(self):
        password_hash = create_password_hash("secret")

        for iterations in (str(PBKDF2_MAX_ITERATIONS + 1), "+600000", "0600000", "٦٠٠٠٠٠"):
            with self.subTest(iterations=iterations):
                malformed_hash = password_hash.replace("$600000$", f"${iterations}$")
                self.assertFalse(verify_password("secret", malformed_hash))

    def test_create_password_hash_rejects_unsupported_iteration_counts(self):
        for iterations in (True, 1.5, 599999, PBKDF2_MAX_ITERATIONS + 1):
            with self.subTest(iterations=iterations):
                with self.assertRaisesRegex(ValueError, "iterations"):
                    create_password_hash("secret", iterations=iterations)

    def test_verify_password_rejects_invalid_hash_equivalence_classes(self):
        valid_hash = create_password_hash("secret")
        algorithm, iterations, salt, digest = valid_hash.split("$")
        invalid_hashes = [
            "",
            "not-a-hash",
            "sha256$390000$salt$digest",
            "pbkdf2_sha256$not-int$salt$digest",
            "pbkdf2_sha256$390000$@@@$digest",
            f"{algorithm}${iterations}${salt}=${digest}",
            f"{algorithm}${iterations}$YQ${digest}",
            f"{algorithm}${iterations}${salt}$YQ",
            f"{valid_hash}$extra",
        ]

        for password_hash in invalid_hashes:
            with self.subTest(password_hash=password_hash):
                self.assertFalse(verify_password("secret", password_hash))

    def test_base64_decoder_requires_canonical_unpadded_base64url(self):
        self.assertEqual(_decode_unpadded_base64("YQ"), b"a")
        for value in (None, "", "YQ==", "@@@", "A", "é", "YR"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _decode_unpadded_base64(value)

    def test_verify_password_rejects_non_string_hash(self):
        self.assertFalse(verify_password("secret", None))

    def test_supported_password_hash_format_requires_canonical_pbkdf2(self):
        self.assertTrue(is_supported_password_hash(create_password_hash("secret")))

        for value in (None, "", "plaintext", "bcrypt$bad", DUMMY_PASSWORD_HASH + "="):
            with self.subTest(value=value):
                self.assertFalse(is_supported_password_hash(value))


class UsersFileStoreTests(TempConfigMixin, unittest.TestCase):
    def test_list_usernames_returns_password_free_snapshot(self):
        users_file = self.temp_path / "users.txt"
        users_file.write_text(
            f"alice:{create_password_hash('secret-password')}\n",
            encoding="utf-8",
        )
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

        self.assertEqual(UsersFileStore().list_usernames(), ("alice",))

    def test_users_file_store_verifies_pbkdf2_hashes(self):
        configure_users_file(self.temp_path)

        store = UsersFileStore()

        self.assertTrue(store.verify("admin", "secret-password"))
        self.assertFalse(store.verify("admin", "bad-password"))
        self.assertFalse(store.verify("missing", "secret-password"))

    def test_concurrent_user_file_cache_loads_are_serialized(self):
        configure_users_file(self.temp_path)
        store = UsersFileStore()
        original_load = store._load_if_needed
        first_inside = threading.Event()
        second_inside = threading.Event()
        release_first = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def controlled_load():
            nonlocal call_count
            with call_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_inside.set()
                if not release_first.wait(timeout=2):
                    raise TimeoutError("first cache load was not released")
            else:
                second_inside.set()
            return original_load()

        with (
            mock.patch.object(store, "_load_if_needed", side_effect=controlled_load),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(store.has_username, "admin")
            try:
                self.assertTrue(first_inside.wait(timeout=1))
                second = executor.submit(store.has_username, "admin")
                self.assertFalse(second_inside.wait(timeout=0.1))
            finally:
                release_first.set()

            self.assertTrue(first.result(timeout=1))
            self.assertTrue(second.result(timeout=1))

    def test_password_comparison_runs_outside_user_file_cache_lock(self):
        configure_users_file(self.temp_path)
        store = UsersFileStore()
        self.assertTrue(store.has_users_file())
        both_started = threading.Event()
        release_comparisons = threading.Event()
        call_lock = threading.Lock()
        call_count = 0

        def blocking_verify(_password, _password_hash):
            nonlocal call_count
            with call_lock:
                call_count += 1
                if call_count == 2:
                    both_started.set()
            if not release_comparisons.wait(timeout=2):
                raise TimeoutError("password comparisons were not released")
            return False

        with (
            mock.patch("src.users_store.verify_password", side_effect=blocking_verify),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(store.verify, "admin", "wrong")
            second = executor.submit(store.verify, "admin", "wrong")
            try:
                self.assertTrue(both_started.wait(timeout=1))
            finally:
                release_comparisons.set()

            self.assertFalse(first.result(timeout=1))
            self.assertFalse(second.result(timeout=1))

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

    def test_startup_validation_fails_when_users_file_is_missing(self):
        users_file = self.temp_path / "missing.txt"
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

        store = UsersFileStore()

        with self.assertRaisesRegex(
            UsersFileConfigurationError,
            f"Authentication users file not found: {users_file}",
        ):
            store.validate_configured_users_file()

    def test_relative_users_file_path_is_resolved_from_working_directory(self):
        store = UsersFileStore()
        with (
            mock.patch("src.users_store.get_users_file_path", return_value="secrets/users.txt"),
            mock.patch("src.users_store.Path.cwd", return_value=self.temp_path),
        ):
            path = store._resolve_users_file()

        self.assertEqual(path, self.temp_path / "secrets" / "users.txt")

    def test_users_file_store_ignores_invalid_password_hashes(self):
        users_file = self.temp_path / "users.txt"
        users_file.write_text("admin:not-a-pbkdf2-hash\n", encoding="utf-8")
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

        store = UsersFileStore()

        self.assertFalse(store.has_users_file())
        self.assertFalse(store.has_username("admin"))
        with self.assertRaisesRegex(
            UsersFileConfigurationError,
            f"Authentication users file has no valid users: {users_file}",
        ):
            store.validate_configured_users_file()

    def test_users_file_store_rejects_non_regular_path(self):
        users_dir = self.temp_path / "users"
        users_dir.mkdir()
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_dir)

        store = UsersFileStore()

        self.assertFalse(store.has_users_file())

        with self.assertRaisesRegex(
            UsersFileConfigurationError,
            f"Authentication users file is not a regular file: {users_dir}",
        ):
            store.validate_configured_users_file()

    def test_startup_validation_fails_when_users_file_has_no_valid_users(self):
        users_file = self.temp_path / "users.txt"
        users_file.write_text("# comment\nmissing-separator\n", encoding="utf-8")
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

        store = UsersFileStore()

        with self.assertRaisesRegex(
            UsersFileConfigurationError,
            f"Authentication users file has no valid users: {users_file}",
        ):
            store.validate_configured_users_file()

    def test_startup_validation_accepts_valid_users_file(self):
        configure_users_file(self.temp_path)

        store = UsersFileStore()

        store.validate_configured_users_file()
        validate_configured_users_file()


class AuthDependencyTests(TempConfigMixin, unittest.TestCase):
    def test_api_key_auth_requires_users_file_identity(self):
        configure_users_file(self.temp_path)

        with self.assertRaises(HTTPException) as context:
            require_api_key_user(make_request(authorization="Bearer admin:secret-password"))

        self.assertEqual(context.exception.status_code, 401)

    def test_api_key_auth_rejects_basic_credentials(self):
        configure_users_file(self.temp_path)
        token = base64.b64encode(b"admin:secret-password").decode("ascii")

        with self.assertRaises(HTTPException) as context:
            require_api_key_user(make_request(authorization=f"Basic {token}"))

        self.assertEqual(context.exception.status_code, 401)

    def test_api_key_auth_rejects_empty_bearer_token(self):
        configure_users_file(self.temp_path)

        with self.assertRaises(HTTPException) as context:
            require_api_key_user(make_request(authorization="Bearer "))

        self.assertEqual(context.exception.status_code, 401)
        self.assertEqual(context.exception.headers, {"WWW-Authenticate": "Bearer"})

    def test_api_key_auth_accepts_generated_api_key(self):
        configure_users_file(self.temp_path)

        key_data = api_key_store.create_key("admin", "test")
        user = require_api_key_user(make_request(authorization=f"Bearer {key_data['api_key']}"))

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "api_key")

    def test_api_key_auth_reports_missing_users_file_as_server_error(self):
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(self.temp_path / "missing.txt")

        with self.assertRaises(HTTPException) as context:
            require_api_key_user(make_request(authorization="Bearer admin:secret-password"))

        self.assertEqual(context.exception.status_code, 500)


class AuthSessionTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)

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

        user = require_session_user(make_request(cookie=cookie))

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "session_cookie")

    async def test_login_ignores_untrusted_forwarded_https_header(self):
        response = Response()

        await service_login(
            make_request(extra_headers={"X-Forwarded-Proto": "https"}),
            response,
            LoginRequest(username="admin", password="secret-password"),
        )

        self.assertNotIn("Secure", response.headers["set-cookie"])

    async def test_login_sets_secure_cookie_for_direct_https(self):
        response = Response()

        await service_login(
            make_request(scheme="https", extra_headers={"host": "testserver"}),
            response,
            LoginRequest(username="admin", password="secret-password"),
        )

        self.assertIn("Secure", response.headers["set-cookie"])

    async def test_login_rejects_blank_username_and_password(self):
        with self.assertRaises(HTTPException) as context:
            await service_login(make_request(), Response(), LoginRequest(username=" ", password=""))

        self.assertEqual(context.exception.status_code, 401)

    async def test_login_rejects_wrong_password(self):
        with self.assertRaises(HTTPException) as context:
            await service_login(
                make_request(),
                Response(),
                LoginRequest(username="admin", password="wrong"),
            )

        self.assertEqual(context.exception.status_code, 401)

    async def test_forwarded_for_header_cannot_bypass_socket_ip_limit(self):
        guard = LoginAttemptGuard(
            global_max_attempts=10,
            ip_max_attempts=1,
            username_max_attempts=10,
            window_seconds=60,
            max_concurrency=2,
        )
        first_request = make_request(extra_headers={"X-Forwarded-For": "192.0.2.1"})
        second_request = make_request(extra_headers={"X-Forwarded-For": "192.0.2.2"})

        with mock.patch("src.auth_router.login_attempt_guard", guard):
            with self.assertRaises(HTTPException) as first:
                await service_login(
                    first_request,
                    Response(),
                    LoginRequest(username="admin", password="wrong"),
                )
            with self.assertRaises(HTTPException) as second:
                await service_login(
                    second_request,
                    Response(),
                    LoginRequest(username="other", password="wrong"),
                )

        self.assertEqual(first.exception.status_code, 401)
        self.assertEqual(second.exception.status_code, 429)

    async def test_password_hashing_runs_off_loop_with_bounded_concurrency(self):
        guard = LoginAttemptGuard(
            global_max_attempts=100,
            ip_max_attempts=100,
            username_max_attempts=100,
            window_seconds=60,
            max_concurrency=2,
        )
        release_hashes = threading.Event()
        started_hashes = threading.Event()
        state_lock = threading.Lock()
        worker_threads = []

        def blocking_verify(_username, _password):
            with state_lock:
                worker_threads.append(threading.get_ident())
                if len(worker_threads) == 2:
                    started_hashes.set()
            if not release_hashes.wait(timeout=2):
                raise TimeoutError("password verification was not released")
            return False

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
        ) as client:
            with (
                mock.patch("src.auth_router.login_attempt_guard", guard),
                mock.patch("src.auth_router.users_store.verify", side_effect=blocking_verify),
            ):
                first = asyncio.create_task(
                    client.post("/auth/login", json={"username": "admin", "password": "wrong"})
                )
                second = asyncio.create_task(
                    client.post("/auth/login", json={"username": "admin", "password": "wrong"})
                )
                try:
                    for _ in range(100):
                        if started_hashes.is_set():
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(started_hashes.is_set())

                    health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
                    rejected = await asyncio.wait_for(
                        client.post(
                            "/auth/login",
                            json={"username": "admin", "password": "wrong"},
                        ),
                        timeout=0.5,
                    )
                finally:
                    release_hashes.set()

                first_response, second_response = await asyncio.gather(first, second)

        self.assertEqual(health.status_code, 200)
        self.assertEqual(rejected.status_code, 429)
        self.assertEqual(rejected.headers["Retry-After"], "1")
        self.assertEqual(first_response.status_code, 401)
        self.assertEqual(second_response.status_code, 401)
        self.assertEqual(len(worker_threads), 2)
        self.assertNotIn(threading.get_ident(), worker_threads)

    async def test_logout_invalidates_session_cookie(self):
        cookie = await self._login_and_get_cookie()
        response = Response()

        result = await service_logout(make_request(cookie=cookie), response)

        self.assertFalse(result["authenticated"])
        with self.assertRaises(HTTPException) as context:
            require_session_user(make_request(cookie=cookie))
        self.assertEqual(context.exception.status_code, 401)

    async def test_api_key_cannot_manage_api_keys(self):
        cookie = await self._login_and_get_cookie()
        session_user = require_session_user(make_request(cookie=cookie))
        created = api_key_store.create_key(session_user.username, "client")

        with self.assertRaises(HTTPException):
            require_session_user(make_request(authorization=f"Bearer {created['api_key']}"))

    async def test_session_cookie_cannot_authenticate_external_api(self):
        cookie = await self._login_and_get_cookie()

        with self.assertRaises(HTTPException) as context:
            require_api_key_user(make_request(cookie=cookie))

        self.assertEqual(context.exception.status_code, 401)

    async def test_require_session_user_and_session_endpoint_return_current_session(self):
        cookie = await self._login_and_get_cookie()

        user = require_session_user(make_request(cookie=cookie))
        result = await get_session(user)

        self.assertEqual(result, {
            "authenticated": True,
            "username": "admin",
            "source": "session_cookie",
        })


if __name__ == "__main__":
    unittest.main()
