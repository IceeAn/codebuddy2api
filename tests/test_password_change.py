
import tests  # 在生产模块导入前隔离测试数据目录。
import unittest
from unittest import mock

import config
import httpx

from src.auth_error_codes import AuthErrorCode
from src.login_security import LoginLimitError
from src.users_store import users_store
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class PasswordChangeApiTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)

    async def _client(self):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
        )

    async def test_formal_login_and_session_report_no_required_change(self):
        async with await self._client() as client:
            login = await client.post(
                "/auth/login",
                json={"username": "admin", "password": "secret-password"},
            )
            session = await client.get("/auth/session")

        self.assertEqual(login.status_code, 200)
        self.assertFalse(login.json()["password_change_required"])
        self.assertFalse(session.json()["password_change_required"])

    async def test_normal_change_requires_and_verifies_current_password(self):
        async with await self._client() as client:
            await client.post(
                "/auth/login",
                json={"username": "admin", "password": "secret-password"},
            )
            missing = await client.post(
                "/auth/change-password", json={"new_password": "new-password"}
            )
            wrong = await client.post(
                "/auth/change-password",
                json={"current_password": "wrong", "new_password": "new-password"},
            )
            unchanged = await client.post(
                "/auth/change-password",
                json={
                    "current_password": "secret-password",
                    "new_password": "secret-password",
                },
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["error_code"], AuthErrorCode.CURRENT_PASSWORD_REQUIRED)
        self.assertEqual(wrong.json()["error_code"], AuthErrorCode.CURRENT_PASSWORD_INCORRECT)
        self.assertEqual(unchanged.json()["error_code"], AuthErrorCode.NEW_PASSWORD_UNCHANGED)

    async def test_success_changes_password_and_logs_out_all_sessions(self):
        async with await self._client() as first, await self._client() as second:
            for client in (first, second):
                response = await client.post(
                    "/auth/login",
                    json={"username": "admin", "password": "secret-password"},
                )
                self.assertEqual(response.status_code, 200)

            changed = await first.post(
                "/auth/change-password",
                json={
                    "current_password": "secret-password",
                    "new_password": "new-password",
                },
            )
            stale = await second.get("/auth/session")
            old_login = await second.post(
                "/auth/login",
                json={"username": "admin", "password": "secret-password"},
            )
            new_login = await second.post(
                "/auth/login",
                json={"username": "admin", "password": "new-password"},
            )

        self.assertEqual(changed.json(), {"password_changed": True, "authenticated": False})
        self.assertIn("Max-Age=0", changed.headers["set-cookie"])
        self.assertEqual(stale.status_code, 401)
        self.assertEqual(stale.json()["error_code"], AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE)
        self.assertEqual(old_login.status_code, 401)
        self.assertEqual(new_login.status_code, 200)

    async def test_new_password_policy_is_eight_to_128_codepoints_without_controls(self):
        async with await self._client() as client:
            await client.post(
                "/auth/login",
                json={"username": "admin", "password": "secret-password"},
            )
            for password in ("short", "valid123\n", "密" * 129):
                with self.subTest(password=password):
                    response = await client.post(
                        "/auth/change-password",
                        json={
                            "current_password": "secret-password",
                            "new_password": password,
                        },
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.json()["error_code"], AuthErrorCode.NEW_PASSWORD_INVALID
                    )

    async def test_pending_bootstrap_is_gated_and_can_change_without_current_password(self):
        # 换用本测试数据库中的全新账号状态。
        with users_store._database().connect() as connection:
            connection.execute("DELETE FROM system_users")
            connection.execute(
                "UPDATE authentication_state SET state = 'uninitialized', "
                "legacy_install_detected = 0, pending_username = NULL"
            )
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(self.temp_path / "missing.txt")
        users_store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="initial-password",
            bootstrap_password_explicit=True,
        )

        async with await self._client() as client:
            status = await client.get("/auth/bootstrap-status")
            login = await client.post(
                "/auth/login",
                json={"username": "admin", "password": "initial-password"},
            )
            gated = await client.get("/api/admin/status")
            gated_anthropic = await client.get(
                "/api/admin/playground/anthropic/v1/models",
                headers={"anthropic-version": "2023-06-01"},
            )
            unchanged = await client.post(
                "/auth/change-password", json={"new_password": "initial-password"}
            )
            forbidden_current = await client.post(
                "/auth/change-password",
                json={"current_password": "admin", "new_password": "new-password"},
            )
            changed = await client.post(
                "/auth/change-password", json={"new_password": "new-password"}
            )

        self.assertEqual(
            status.json(), {"bootstrap_required": True, "bootstrap_expired": False}
        )
        self.assertTrue(login.json()["password_change_required"])
        self.assertEqual(gated.status_code, 403)
        self.assertEqual(gated.json()["error_code"], AuthErrorCode.PASSWORD_CHANGE_REQUIRED)
        self.assertEqual(gated_anthropic.status_code, 403)
        self.assertEqual(gated_anthropic.json()["error"]["type"], "permission_error")
        self.assertEqual(
            unchanged.json()["error_code"], AuthErrorCode.NEW_PASSWORD_UNCHANGED
        )
        self.assertEqual(
            forbidden_current.json()["error_code"],
            AuthErrorCode.CURRENT_PASSWORD_NOT_ALLOWED,
        )
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(users_store.bootstrap_status().bootstrap_required)

    async def test_expired_bootstrap_rejects_correct_login_and_deletes_session_cookie(self):
        with users_store._database().connect() as connection:
            connection.execute("DELETE FROM system_users")
            connection.execute(
                "UPDATE authentication_state SET state = 'uninitialized', "
                "legacy_install_detected = 0, pending_username = NULL"
            )
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(self.temp_path / "missing.txt")
        users_store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="admin",
            bootstrap_password_explicit=False,
        )
        users_store._bootstrap_deadline = 0

        async with await self._client() as client:
            response = await client.post(
                "/auth/login", json={"username": "admin", "password": "admin"}
            )
            status = await client.get("/auth/bootstrap-status")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], AuthErrorCode.BOOTSTRAP_EXPIRED)
        self.assertEqual(
            status.json(), {"bootstrap_required": True, "bootstrap_expired": True}
        )

    async def test_existing_bootstrap_session_expires_with_specific_error(self):
        with users_store._database().connect() as connection:
            connection.execute("DELETE FROM system_users")
            connection.execute(
                "UPDATE authentication_state SET state = 'uninitialized', "
                "legacy_install_detected = 0, pending_username = NULL"
            )
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(self.temp_path / "missing.txt")
        users_store.initialize_service(
            bootstrap_enabled=True,
            bootstrap_username="admin",
            bootstrap_password="admin",
            bootstrap_password_explicit=False,
        )

        async with await self._client() as client:
            login = await client.post(
                "/auth/login", json={"username": "admin", "password": "admin"}
            )
            self.assertEqual(login.status_code, 200)
            users_store._bootstrap_deadline = 0
            response = await client.get("/auth/session")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error_code"], AuthErrorCode.BOOTSTRAP_EXPIRED)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertIn("Max-Age=0", response.headers["set-cookie"])

    async def test_password_change_uses_shared_rate_and_concurrency_limits(self):
        async with await self._client() as client:
            await client.post(
                "/auth/login",
                json={"username": "admin", "password": "secret-password"},
            )
            with mock.patch(
                "src.auth_router.login_attempt_guard.record_attempt",
                side_effect=LoginLimitError(7),
            ):
                limited = await client.post(
                    "/auth/change-password",
                    json={
                        "current_password": "secret-password",
                        "new_password": "new-password",
                    },
                )
            with mock.patch(
                "src.auth_router.login_attempt_guard.try_acquire", return_value=False
            ):
                busy = await client.post(
                    "/auth/change-password",
                    json={
                        "current_password": "secret-password",
                        "new_password": "new-password",
                    },
                )

        self.assertEqual(limited.status_code, 429)
        self.assertEqual(
            limited.json()["error_code"], AuthErrorCode.PASSWORD_CHANGE_RATE_LIMITED
        )
        self.assertEqual(limited.headers["retry-after"], "7")
        self.assertEqual(busy.status_code, 429)
        self.assertEqual(
            busy.json()["error_code"], AuthErrorCode.PASSWORD_CHANGE_RATE_LIMITED
        )
        self.assertEqual(busy.headers["retry-after"], "1")

    async def test_disconnect_before_cas_leaves_password_unchanged(self):
        async with await self._client() as client:
            await client.post(
                "/auth/login",
                json={"username": "admin", "password": "secret-password"},
            )
            with (
                mock.patch(
                    "src.auth_router.Request.is_disconnected",
                    new=mock.AsyncMock(return_value=True),
                ),
                mock.patch.object(
                    users_store, "replace_password", wraps=users_store.replace_password
                ) as replace_password,
            ):
                response = await client.post(
                    "/auth/change-password",
                    json={
                        "current_password": "secret-password",
                        "new_password": "new-password",
                    },
                )

        self.assertEqual(response.status_code, 499)
        replace_password.assert_not_called()
        self.assertTrue(users_store.verify("admin", "secret-password"))

    async def test_concurrent_password_change_loser_gets_specific_unauthorized_error(self):
        async with await self._client() as client:
            await client.post(
                "/auth/login",
                json={"username": "admin", "password": "secret-password"},
            )
            with mock.patch.object(users_store, "replace_password", return_value=None):
                response = await client.post(
                    "/auth/change-password",
                    json={
                        "current_password": "secret-password",
                        "new_password": "new-password",
                    },
                )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json()["error_code"], AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE
        )
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertIn("Max-Age=0", response.headers["set-cookie"])


if __name__ == "__main__":
    unittest.main()
