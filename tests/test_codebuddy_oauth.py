import base64
import json
import unittest
from unittest import mock

from src.auth_types import AuthenticatedUser
from fastapi import HTTPException

import src.codebuddy_oauth as oauth
from src.codebuddy_auth_router import cancel_auth, poll_for_token, start_device_auth
from src.codebuddy_oauth import (
    AuthStateStore,
    AuthStartLimitError,
    CodeBuddyAuthClient,
    CodeBuddyTokenSaver,
    TokenParser,
    is_safe_external_auth_url,
)


def jwt_with_payload(payload):
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{encoded_payload}.signature"


class FakeAuthStateResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeTokenResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.text = json.dumps(payload)
        self._payload = payload

    def json(self):
        return self._payload


class AuthStateStoreTests(unittest.TestCase):
    def register_state(self, store, state, user):
        reservation = store.begin_start(user)
        self.assertTrue(store.finish_start(reservation, state, user))

    def test_auth_state_ttl_is_ten_minutes(self):
        self.assertEqual(oauth.AUTH_STATE_TTL_SECONDS, 600)

    def test_start_reservations_limit_each_user_to_three_active_flows(self):
        store = AuthStateStore(max_active_per_user=3, max_start_attempts=100)
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with mock.patch("src.codebuddy_oauth.time.time", return_value=100):
            reservations = [store.begin_start(user) for _ in range(3)]
            with self.assertRaises(AuthStartLimitError) as raised:
                store.begin_start(user)

        self.assertEqual(raised.exception.retry_after, 600)
        store.cancel_start(reservations[0])
        with mock.patch("src.codebuddy_oauth.time.time", return_value=101):
            self.assertTrue(store.begin_start(user))

    def test_start_rate_limit_counts_all_attempts_and_returns_retry_after(self):
        store = AuthStateStore(max_active_per_user=100, max_start_attempts=5, start_window_seconds=60)
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with mock.patch("src.codebuddy_oauth.time.time", return_value=100):
            for _ in range(5):
                reservation = store.begin_start(user)
                store.cancel_start(reservation)
            with self.assertRaises(AuthStartLimitError) as raised:
                store.begin_start(user)

        self.assertEqual(raised.exception.retry_after, 60)
        with mock.patch("src.codebuddy_oauth.time.time", return_value=160):
            reservation = store.begin_start(user)
        self.assertTrue(reservation)

    def test_start_reservation_commits_state_atomically_and_rejects_duplicates(self):
        store = AuthStateStore()
        user = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")
        reservation = store.begin_start(user)

        self.assertTrue(store.finish_start(reservation, "state", user))
        duplicate = store.begin_start(other)
        self.assertFalse(store.finish_start(duplicate, "state", other))
        self.assertTrue(store.validate_owner("state", user))
        self.assertFalse(store.validate_owner("state", other))
        self.assertFalse(store.finish_start("missing", "other-state", user))

    def test_cleanup_removes_expired_start_reservations(self):
        store = AuthStateStore(ttl_seconds=10)
        user = AuthenticatedUser(username="alice", source="session_cookie")
        with mock.patch("src.codebuddy_oauth.time.time", return_value=100):
            reservation = store.begin_start(user)

        store.cleanup_expired(111)

        self.assertNotIn(reservation, store._starting)

    def test_auth_state_store_validates_same_owner_and_rejects_other_user(self):
        store = AuthStateStore(ttl_seconds=60)
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")

        self.register_state(store, "state", owner)

        self.assertTrue(store.validate_owner("state", owner))
        self.assertFalse(store.validate_owner("state", other))
        self.assertFalse(store.validate_owner("missing", owner))

    def test_auth_state_store_expires_only_after_ttl_boundary(self):
        store = AuthStateStore(ttl_seconds=60)
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with mock.patch("src.codebuddy_oauth.time.time", return_value=100):
            self.register_state(store, "state", user)

        with mock.patch("src.codebuddy_oauth.time.time", return_value=160):
            self.assertTrue(store.validate_owner("state", user))

        with mock.patch("src.codebuddy_oauth.time.time", return_value=161):
            self.assertFalse(store.validate_owner("state", user))

    def test_consumed_auth_state_cannot_be_polled_or_registered_again(self):
        store = AuthStateStore()
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")
        self.register_state(store, "state", owner)

        self.assertTrue(store.consume("state", owner))

        self.assertFalse(store.validate_owner("state", owner))
        duplicate = store.begin_start(other)
        self.assertFalse(store.finish_start(duplicate, "state", other))
        self.assertFalse(store.consume("state", owner))

    def test_auth_state_can_only_be_consumed_by_its_owner(self):
        store = AuthStateStore()
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")
        self.register_state(store, "state", owner)

        self.assertFalse(store.consume("state", other))
        self.assertTrue(store.validate_owner("state", owner))

    def test_missing_auth_state_cannot_be_consumed(self):
        store = AuthStateStore()
        owner = AuthenticatedUser(username="alice", source="session_cookie")

        self.assertFalse(store.consume("missing", owner))

    def test_consumed_auth_state_retention_starts_when_consumed(self):
        store = AuthStateStore(ttl_seconds=60)
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")

        with mock.patch("src.codebuddy_oauth.time.time", return_value=100):
            self.register_state(store, "state", owner)
        with mock.patch("src.codebuddy_oauth.time.time", return_value=120):
            self.assertTrue(store.consume("state", owner))
        with mock.patch("src.codebuddy_oauth.time.time", return_value=180):
            duplicate = store.begin_start(other)
            self.assertFalse(store.finish_start(duplicate, "state", other))
        with mock.patch("src.codebuddy_oauth.time.time", return_value=181):
            self.register_state(store, "state", other)

    def test_auth_state_store_rejects_duplicate_without_overwriting_owner(self):
        store = AuthStateStore()
        first_owner = AuthenticatedUser(username="alice", source="session_cookie")
        second_owner = AuthenticatedUser(username="bob", source="session_cookie")

        self.register_state(store, "state", first_owner)
        duplicate = store.begin_start(second_owner)
        self.assertFalse(store.finish_start(duplicate, "state", second_owner))

        self.assertTrue(store.validate_owner("state", first_owner))
        self.assertFalse(store.validate_owner("state", second_owner))

    def test_legacy_remember_api_is_removed(self):
        self.assertFalse(hasattr(AuthStateStore(), "remember"))
        self.assertFalse(hasattr(oauth, "remember_auth_state"))

    def test_progress_is_owner_scoped_and_cleared_when_consumed(self):
        store = AuthStateStore()
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")
        self.register_state(store, "state", owner)

        self.assertTrue(store.set_progress("state", owner, {"access_token": "secret"}))
        self.assertEqual(store.get_progress("state", owner), {"access_token": "secret"})
        self.assertIsNone(store.get_progress("state", other))
        self.assertFalse(store.set_progress("missing", owner, {"access_token": "secret"}))
        self.assertTrue(store.consume("state", owner))
        self.assertFalse(store.set_progress("state", owner, {"access_token": "new"}))
        self.assertIsNone(store.get_progress("state", owner))


class CodeBuddyAuthClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_client_accepts_async_shared_client_factory(self):
        shared_client = mock.Mock()
        factory = mock.AsyncMock(return_value=shared_client)

        client = await CodeBuddyAuthClient(http_client_factory=factory)._get_http_client()

        self.assertIs(client, shared_client)
        factory.assert_awaited_once_with()

    async def test_state_request_matches_cli_without_nonce(self):
        fake_client = mock.Mock()
        fake_client.post = mock.AsyncMock(return_value=FakeAuthStateResponse({
            "code": 0,
            "data": {"state": "state", "authUrl": "https://codebuddy.example/auth"},
        }))

        await CodeBuddyAuthClient()._request_state(fake_client, {"X-Test": "1"})

        args, kwargs = fake_client.post.await_args
        self.assertEqual(args[0], f"{oauth.get_codebuddy_auth_state_endpoint()}?platform=CLI")
        self.assertEqual(kwargs["json"], {})

    async def test_poll_runs_token_account_and_accounts_stages(self):
        responses = [
            FakeTokenResponse({
                "code": 0,
                "data": {
                    "accessToken": "access",
                    "refreshToken": "refresh",
                    "tokenType": "Bearer",
                    "expiresIn": 7200,
                    "refreshExpiresIn": 86400,
                    "domain": "copilot.tencent.com",
                },
            }),
            FakeTokenResponse({
                "code": 0,
                "data": {
                    "uid": "account-uid",
                    "nickname": "Alice",
                    "type": "ultimate",
                    "enterpriseId": "enterprise-1",
                    "enterpriseName": "Example Corp",
                    "departmentFullName": "研发部",
                },
            }),
            FakeTokenResponse({
                "code": 0,
                "data": {
                    "accounts": [
                        {
                            "uid": "account-uid",
                            "nickname": "Alice",
                            "type": "ultimate",
                            "enterpriseId": "enterprise-1",
                            "pluginEnabled": True,
                            "lastLogin": True,
                        },
                        {"uid": "disabled", "type": "personal", "pluginEnabled": False},
                    ],
                },
            }),
        ]
        fake_client = mock.Mock()
        fake_client.get = mock.AsyncMock(side_effect=responses)
        client = CodeBuddyAuthClient(http_client_factory=lambda: fake_client)

        result = await client.poll_status("state")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["token_data"]["account"]["uid"], "account-uid")
        self.assertEqual(len(result["token_data"]["accounts"]), 1)
        self.assertEqual(
            [call.args[0] for call in fake_client.get.await_args_list],
            [
                f"{oauth.get_codebuddy_auth_token_endpoint()}?state=state",
                f"{oauth.get_codebuddy_login_account_endpoint()}?state=state",
                oauth.get_codebuddy_accounts_endpoint(),
            ],
        )

    async def test_account_pending_returns_sensitive_progress_for_server_storage(self):
        fake_client = mock.Mock()
        fake_client.get = mock.AsyncMock(side_effect=[
            FakeTokenResponse({
                "code": 0,
                "data": {"accessToken": "secret", "tokenType": "Bearer"},
            }),
            FakeTokenResponse({"code": 12151, "msg": "waiting"}),
        ])

        result = await CodeBuddyAuthClient(
            http_client_factory=lambda: fake_client,
        ).poll_status("state")

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["stage"], "account")
        self.assertEqual(result["progress"]["access_token"], "secret")

    async def test_poll_resumes_from_server_side_token_progress_without_repolling_token(self):
        fake_client = mock.Mock()
        fake_client.get = mock.AsyncMock(side_effect=[
            FakeTokenResponse({"code": 12151, "msg": "waiting"}),
        ])

        result = await CodeBuddyAuthClient(
            http_client_factory=lambda: fake_client,
        ).poll_status("state", {"access_token": "secret", "domain": "example.com"})

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["stage"], "account")
        self.assertEqual(fake_client.get.await_count, 1)
        self.assertIn("/v2/plugin/login/account?state=state", fake_client.get.await_args.args[0])

    async def test_known_business_errors_have_stable_meanings(self):
        expected = {
            12005: "license_seat_limit",
            11212: "license_expired",
            11216: "trial_expired",
            10081: "ip_restricted",
        }
        for code, error_name in expected.items():
            with self.subTest(code=code):
                fake_client = mock.Mock()
                fake_client.get = mock.AsyncMock(
                    return_value=FakeTokenResponse({"code": code, "msg": "raw upstream message"})
                )
                result = await CodeBuddyAuthClient(
                    http_client_factory=lambda: fake_client,
                ).poll_status("state")
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["error"], error_name)
                self.assertNotIn("raw upstream message", result["message"])
    def test_auth_url_allowlist_accepts_only_absolute_http_and_https_without_userinfo(self):
        self.assertTrue(is_safe_external_auth_url("https://codebuddy.example/auth?state=1"))
        self.assertTrue(is_safe_external_auth_url("http://127.0.0.1:8080/auth"))

        for value in (
            "javascript:alert(1)",
            "data:text/html,unsafe",
            "/relative/auth",
            "//codebuddy.example/auth",
            "https://user:password@codebuddy.example/auth",
            "https://codebuddy.example/\nunsafe",
            "https://codebuddy.example:invalid/auth",
            " https://codebuddy.example/auth",
            "",
            None,
        ):
            with self.subTest(value=value):
                self.assertFalse(is_safe_external_auth_url(value))

    async def test_request_state_rejects_unsafe_auth_url_before_returning_state(self):
        for auth_url in (
            "javascript:alert(document.domain)",
            "data:text/html,unsafe",
            "/relative/auth",
            "https://user:password@codebuddy.example/auth",
        ):
            with self.subTest(auth_url=auth_url):
                fake_client = mock.Mock()
                fake_client.post = mock.AsyncMock(return_value=FakeAuthStateResponse({
                    "code": 0,
                    "data": {"state": "must-not-register", "authUrl": auth_url},
                }))

                result = await CodeBuddyAuthClient()._request_state(fake_client, {"X-Test": "1"})

                self.assertEqual(result, {"auth_state": None, "auth_url": None})

    async def test_oauth_reuses_injected_shared_client_without_connection_close(self):
        class FakeAsyncClient:
            def __init__(self):
                self.responses = [
                FakeAuthStateResponse({
                    "code": 0,
                    "data": {"state": "state-1", "authUrl": "https://codebuddy.example/auth"},
                }),
                FakeTokenResponse({"code": 11217, "msg": "login ing..."}),
                ]
                self.requests = []

            async def post(self, *_args, **kwargs):
                self.requests.append(kwargs)
                return self.responses.pop(0)

            async def get(self, *_args, **kwargs):
                self.requests.append(kwargs)
                return self.responses.pop(0)

        shared_client = FakeAsyncClient()
        client = CodeBuddyAuthClient(http_client_factory=lambda: shared_client)

        start_result = await client.start_auth()
        await client.poll_status("state-1")

        self.assertEqual(start_result["expires_in"], 600)
        self.assertEqual(len(shared_client.requests), 2)
        for request in shared_client.requests:
            self.assertNotIn("Connection", request["headers"])

    async def test_each_start_auth_call_requests_state_only_once(self):
        post_count = 0

        class FakeAsyncClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def post(self, *_args, **_kwargs):
                nonlocal post_count
                post_count += 1
                return FakeAuthStateResponse({
                    "code": 0,
                    "data": {
                        "state": "state-1",
                        "authUrl": "https://codebuddy.example/auth",
                    },
                })

        shared_client = FakeAsyncClient()
        client = CodeBuddyAuthClient(http_client_factory=lambda: shared_client)
        first_result = await client.start_auth()
        second_result = await client.start_auth()

        self.assertTrue(first_result["success"])
        self.assertTrue(second_result["success"])
        self.assertEqual(first_result["auth_state"], "state-1")
        self.assertEqual(second_result["auth_state"], "state-1")
        self.assertEqual(post_count, 2)

    async def test_start_auth_reports_incomplete_upstream_response(self):
        client = CodeBuddyAuthClient(http_client_factory=lambda: mock.Mock())

        with mock.patch.object(
            client,
            "_request_state",
            new=mock.AsyncMock(return_value={"auth_state": None, "auth_url": None}),
        ):
            result = await client.start_auth()

        self.assertEqual(result, {
            "success": False,
            "error": "auth_start_failed",
            "message": "无法启动认证流程",
        })

    async def test_start_auth_maps_client_exception(self):
        def broken_factory():
            raise RuntimeError("敏感的认证客户端异常详情")

        with self.assertLogs("src.codebuddy_oauth", level="ERROR") as captured:
            result = await CodeBuddyAuthClient(http_client_factory=broken_factory).start_auth()

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "认证启动失败，请稍后重试")
        self.assertNotIn("敏感的认证客户端异常详情", result["message"])
        self.assertIsNotNone(captured.records[0].exc_info)

    async def test_request_state_rejects_invalid_responses(self):
        responses = [
            FakeAuthStateResponse({}, status_code=503),
            FakeAuthStateResponse([]),
            FakeAuthStateResponse({"code": 1, "data": {}}),
            FakeAuthStateResponse({"code": 0, "data": "invalid"}),
        ]

        for response in responses:
            with self.subTest(response=response._payload):
                fake_client = mock.Mock()
                fake_client.post = mock.AsyncMock(return_value=response)

                result = await CodeBuddyAuthClient()._request_state(fake_client, {"X-Test": "1"})

                self.assertEqual(result, {"auth_state": None, "auth_url": None})

    async def test_poll_status_maps_http_success_pending_unknown_and_failure(self):
        responses = [
            (
                FakeTokenResponse({}, status_code=502),
                "error",
            ),
            (
                FakeTokenResponse({"code": 11217}),
                "pending",
            ),
            (
                FakeTokenResponse({"code": 0, "data": {}}),
                "error",
            ),
        ]

        class FakeAsyncClient:
            def __init__(self, response, **_kwargs):
                self.response = response

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def get(self, *_args, **_kwargs):
                return self.response

        for response, expected_status in responses:
            with self.subTest(expected_status=expected_status):
                client = FakeAsyncClient(response)
                result = await CodeBuddyAuthClient(
                    http_client_factory=lambda: client,
                ).poll_status("state")

                self.assertEqual(result["status"], expected_status)
                if response.status_code == 502:
                    self.assertEqual(result["error"], "auth_unavailable")

    async def test_poll_status_maps_client_exception(self):
        def broken_factory():
            raise RuntimeError("network unavailable")

        result = await CodeBuddyAuthClient(http_client_factory=broken_factory).poll_status("state")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "认证状态查询失败")

    async def test_poll_status_rejects_invalid_responses_at_each_stage(self):
        token = FakeTokenResponse({
            "code": 0,
            "data": {"accessToken": "secret", "tokenType": "Bearer"},
        })
        account = FakeTokenResponse({"code": 0, "data": {"uid": "account-uid"}})
        cases = [
            ([FakeTokenResponse([])], "invalid_auth_response"),
            ([FakeTokenResponse({"code": 99999})], "invalid_auth_response"),
            ([token, FakeTokenResponse({}, status_code=503)], "auth_unavailable"),
            ([token, FakeTokenResponse([])], "invalid_auth_response"),
            ([token, FakeTokenResponse({"code": 11212})], "license_expired"),
            ([token, FakeTokenResponse({"code": 0, "data": {}})], "invalid_auth_response"),
            ([token, account, FakeTokenResponse({}, status_code=503)], None),
            ([token, account, FakeTokenResponse([])], "invalid_auth_response"),
            ([token, account, FakeTokenResponse({"code": 11216})], "trial_expired"),
            ([token, account, FakeTokenResponse({"code": 0, "data": {}})], "invalid_auth_response"),
        ]
        for responses, expected_error in cases:
            with self.subTest(expected_error=expected_error, responses=responses):
                fake_client = mock.Mock()
                fake_client.get = mock.AsyncMock(side_effect=responses)
                result = await CodeBuddyAuthClient(
                    http_client_factory=lambda fake_client=fake_client: fake_client,
                ).poll_status("state")
                if expected_error is None:
                    self.assertEqual(result["status"], "pending")
                    self.assertEqual(result["stage"], "accounts")
                else:
                    self.assertEqual(result["status"], "error")
                    self.assertEqual(result["error"], expected_error)

    async def test_poll_exception_after_token_preserves_server_side_progress(self):
        invalid_account_response = mock.Mock(status_code=200)
        invalid_account_response.json.side_effect = RuntimeError("invalid json")
        fake_client = mock.Mock()
        fake_client.get = mock.AsyncMock(side_effect=[
            FakeTokenResponse({"code": 0, "data": {"accessToken": "secret"}}),
            invalid_account_response,
        ])

        result = await CodeBuddyAuthClient(
            http_client_factory=lambda: fake_client,
        ).poll_status("state")

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["progress"]["access_token"], "secret")

    def test_token_parser_preserves_optional_compatibility_fields(self):
        self.assertEqual(TokenParser._normalize_epoch(1_780_000_000_000), 1_780_000_000)
        self.assertEqual(TokenParser._site_type("https://www.codebuddy.ai"), "international")
        self.assertEqual(TokenParser._site_type("https://custom.example"), "custom")
        self.assertIsNone(TokenParser._normalize_account("invalid"))
        self.assertIsNone(TokenParser._normalize_account({"uid": ""}))

        credential = TokenParser.build_credential_data({
            "bearer_token": "opaque-token",
            "accounts": [None, {"uid": "account", "type": "personal"}],
            "full_response": {"legacy": True},
            "refresh_expires_in": 100,
            "created_at": 1000,
        })
        self.assertEqual(credential["compatibility_data"], {
            "legacy_full_response": {"legacy": True},
        })
        self.assertEqual(len(credential["accounts"]), 1)
        self.assertEqual(credential["refresh_expires_at"], 1100)


class CodeBuddyAuthRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_device_auth_returns_429_with_retry_after_when_limited(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        with mock.patch.object(
            oauth.auth_state_store,
            "begin_start",
            side_effect=AuthStartLimitError(retry_after=17),
        ):
            with self.assertRaises(HTTPException) as raised:
                await start_device_auth(user)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.headers, {"Retry-After": "17"})

    async def test_start_device_auth_registers_new_state(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        upstream_result = {
            "success": True,
            "auth_state": "new-state",
            "verification_uri_complete": "https://codebuddy.example/auth",
        }

        with (
            mock.patch(
                "src.codebuddy_auth_router.start_codebuddy_auth",
                new=mock.AsyncMock(return_value=upstream_result),
            ),
            mock.patch.object(oauth.auth_state_store, "begin_start", return_value="reservation"),
            mock.patch.object(oauth.auth_state_store, "finish_start", return_value=True) as finish,
            mock.patch.object(oauth.auth_state_store, "cancel_start") as cancel_start,
        ):
            result = await start_device_auth(user)

        self.assertEqual(result, upstream_result)
        finish.assert_called_once_with("reservation", "new-state", user)
        cancel_start.assert_called_once_with("reservation")

    async def test_start_device_auth_rejects_already_reserved_state(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        upstream_result = {
            "success": True,
            "auth_state": "duplicate-state",
            "verification_uri_complete": "https://codebuddy.example/auth",
        }

        with (
            mock.patch(
                "src.codebuddy_auth_router.start_codebuddy_auth",
                new=mock.AsyncMock(return_value=upstream_result),
            ),
            mock.patch.object(oauth.auth_state_store, "begin_start", return_value="reservation"),
            mock.patch.object(oauth.auth_state_store, "finish_start", return_value=False),
            mock.patch.object(oauth.auth_state_store, "cancel_start"),
        ):
            result = await start_device_auth(user)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "duplicate_auth_state")
        self.assertNotIn("verification_uri_complete", result)

    async def test_start_device_auth_rejects_success_response_without_state(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with (
            mock.patch(
                "src.codebuddy_auth_router.start_codebuddy_auth",
                new=mock.AsyncMock(return_value={"success": True}),
            ),
            mock.patch.object(oauth.auth_state_store, "begin_start", return_value="reservation"),
            mock.patch.object(oauth.auth_state_store, "finish_start") as finish,
            mock.patch.object(oauth.auth_state_store, "cancel_start"),
        ):
            result = await start_device_auth(user)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_auth_state")
        finish.assert_not_called()

    async def test_start_device_auth_returns_upstream_failure(self):
        upstream_result = {"success": False, "error": "unavailable"}
        with (
            mock.patch(
                "src.codebuddy_auth_router.start_codebuddy_auth",
                new=mock.AsyncMock(return_value=upstream_result),
            ),
            mock.patch.object(oauth.auth_state_store, "begin_start", return_value="reservation"),
            mock.patch.object(oauth.auth_state_store, "cancel_start"),
        ):
            result = await start_device_auth(
                AuthenticatedUser(username="alice", source="session_cookie")
            )

        self.assertIs(result, upstream_result)

    async def test_start_device_auth_maps_unexpected_exception(self):
        with (
            mock.patch(
                "src.codebuddy_auth_router.start_codebuddy_auth",
                new=mock.AsyncMock(side_effect=RuntimeError("敏感的认证路由异常详情")),
            ),
            mock.patch.object(oauth.auth_state_store, "begin_start", return_value="reservation"),
            mock.patch.object(oauth.auth_state_store, "cancel_start"),
        ):
            result = await start_device_auth(
                AuthenticatedUser(username="alice", source="session_cookie")
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "认证启动失败，请稍后重试")
        self.assertNotIn("敏感的认证路由异常详情", result["message"])

    async def test_successful_poll_consumes_state_before_saving_token(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        token_data = {"access_token": "secret", "token_type": "Bearer"}
        save_token = mock.AsyncMock(return_value=True)

        with (
            mock.patch("src.codebuddy_auth_router.validate_auth_state_owner", return_value=True),
            mock.patch(
                "src.codebuddy_auth_router.poll_codebuddy_auth_status",
                new=mock.AsyncMock(return_value={"status": "success", "token_data": token_data}),
            ),
            mock.patch("src.codebuddy_auth_router.consume_auth_state", return_value=True) as consume,
            mock.patch("src.codebuddy_auth_router.save_codebuddy_token", new=save_token),
        ):
            response = await poll_for_token(auth_state="state", _user=user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.body),
            {"saved": True, "message": "认证成功"},
        )
        consume.assert_called_once_with("state", user)
        save_token.assert_awaited_once_with(token_data, user)

    async def test_successful_poll_never_exposes_sensitive_token_fields(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        token_data = {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "user_info": {"email": "alice@example.com"},
            "full_response": {"private": "value"},
        }

        with (
            mock.patch("src.codebuddy_auth_router.validate_auth_state_owner", return_value=True),
            mock.patch(
                "src.codebuddy_auth_router.poll_codebuddy_auth_status",
                new=mock.AsyncMock(return_value={"status": "success", "token_data": token_data}),
            ),
            mock.patch("src.codebuddy_auth_router.consume_auth_state", return_value=True),
            mock.patch(
                "src.codebuddy_auth_router.save_codebuddy_token",
                new=mock.AsyncMock(return_value=True),
            ),
        ):
            response = await poll_for_token(auth_state="state", _user=user)

        body = json.loads(response.body)
        self.assertEqual(body, {"saved": True, "message": "认证成功"})
        for field in ("access_token", "refresh_token", "user_info", "full_response"):
            self.assertNotIn(field, body)

    async def test_successful_poll_returns_500_when_token_save_fails(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with (
            mock.patch("src.codebuddy_auth_router.validate_auth_state_owner", return_value=True),
            mock.patch(
                "src.codebuddy_auth_router.poll_codebuddy_auth_status",
                new=mock.AsyncMock(
                    return_value={"status": "success", "token_data": {"access_token": "secret"}}
                ),
            ),
            mock.patch("src.codebuddy_auth_router.consume_auth_state", return_value=True) as consume,
            mock.patch(
                "src.codebuddy_auth_router.save_codebuddy_token",
                new=mock.AsyncMock(return_value=False),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await poll_for_token(auth_state="state", _user=user)

        self.assertEqual(raised.exception.status_code, 500)
        consume.assert_called_once_with("state", user)

    async def test_cancel_auth_consumes_owned_state(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with mock.patch("src.codebuddy_auth_router.consume_auth_state", return_value=True) as consume:
            response = await cancel_auth(auth_state="state", _user=user)

        self.assertEqual(response, {"cancelled": True})
        consume.assert_called_once_with("state", user)

    async def test_cancel_auth_rejects_missing_foreign_and_already_consumed_state(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with self.assertRaises(HTTPException) as missing:
            await cancel_auth(auth_state=None, _user=user)
        self.assertEqual(missing.exception.status_code, 400)

        for state in ("foreign", "already-consumed"):
            with self.subTest(state=state):
                with mock.patch("src.codebuddy_auth_router.consume_auth_state", return_value=False):
                    with self.assertRaises(HTTPException) as rejected:
                        await cancel_auth(auth_state=state, _user=user)
                self.assertEqual(rejected.exception.status_code, 403)

    async def test_successful_poll_fails_closed_when_state_cannot_be_consumed(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        save_token = mock.AsyncMock()

        with (
            mock.patch("src.codebuddy_auth_router.validate_auth_state_owner", return_value=True),
            mock.patch(
                "src.codebuddy_auth_router.poll_codebuddy_auth_status",
                new=mock.AsyncMock(
                    return_value={"status": "success", "token_data": {"access_token": "secret"}}
                ),
            ),
            mock.patch("src.codebuddy_auth_router.consume_auth_state", return_value=False),
            mock.patch("src.codebuddy_auth_router.save_codebuddy_token", new=save_token),
        ):
            with self.assertRaises(HTTPException) as raised:
                await poll_for_token(auth_state="state", _user=user)

        self.assertEqual(raised.exception.status_code, 409)
        save_token.assert_not_awaited()

    async def test_poll_rejects_missing_or_foreign_state(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")

        missing = await poll_for_token(
            device_code="legacy",
            code_verifier=None,
            auth_state=None,
            _user=user,
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(json.loads(missing.body)["error"], "missing_parameters")

        with mock.patch("src.codebuddy_auth_router.validate_auth_state_owner", return_value=False):
            with self.assertRaises(HTTPException) as raised:
                await poll_for_token(code_verifier="legacy", auth_state="foreign", _user=user)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_poll_maps_invalid_token_pending_and_error_results(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        results = [
            ({"status": "success", "token_data": {"scope": "openid"}}, "invalid_token_response"),
            ({"status": "success", "token_data": {}}, "auth_error"),
            ({"status": "pending", "code": 11217}, "authorization_pending"),
            (
                {
                    "status": "error",
                    "message": "sensitive-upstream-message",
                    "response_text": "sensitive-upstream-body",
                },
                "auth_error",
            ),
        ]

        for poll_result, expected_error in results:
            with self.subTest(expected_error=expected_error):
                with (
                    mock.patch("src.codebuddy_auth_router.validate_auth_state_owner", return_value=True),
                    mock.patch(
                        "src.codebuddy_auth_router.poll_codebuddy_auth_status",
                        new=mock.AsyncMock(return_value=poll_result),
                    ),
                ):
                    response = await poll_for_token(
                        device_code=None,
                        code_verifier=None,
                        auth_state="state",
                        _user=user,
                    )

                expected_status = 502 if poll_result.get("status") == "error" else 400
                self.assertEqual(response.status_code, expected_status)
                body = json.loads(response.body)
                self.assertEqual(body["error"], expected_error)
                self.assertNotIn("sensitive-upstream", json.dumps(body))

class TokenParserTests(unittest.TestCase):
    def test_successful_jwt_parse_does_not_log_personal_information(self):
        token = jwt_with_payload({
            "sub": "private-user-id",
            "email": "private@example.com",
            "name": "Private Name",
        })

        with self.assertLogs("src.codebuddy_oauth", level="INFO") as captured:
            user_id, user_info = TokenParser._extract_user_info(token, {})

        self.assertEqual(user_id, "private-user-id")
        self.assertEqual(user_info["email"], "private@example.com")
        logs = "\n".join(captured.output)
        self.assertIn("成功解析JWT", logs)
        self.assertNotIn("private-user-id", logs)
        self.assertNotIn("private@example.com", logs)
        self.assertNotIn("Private Name", logs)

    def test_build_credential_data_uses_jwt_sub_for_user_id_and_keeps_display_fields(self):
        token = jwt_with_payload({
            "sub": "sub-id",
            "email": "alice@example.com",
            "preferred_username": "alice",
            "nickname": "AliceNick",
            "name": "Alice",
            "sid": "session",
            "iss": "https://accounts.example.com/oauth/sso-enterprise-1",
        })

        with mock.patch("src.codebuddy_oauth.time.time", return_value=123):
            data = TokenParser.build_credential_data({
                "access_token": token,
                "expires_in": 3600,
                "refresh_token": "refresh",
                "token_type": "Bearer",
                "scope": "openid",
            })

        self.assertEqual(data["bearer_token"], token)
        self.assertEqual(data["user_id"], "sub-id")
        self.assertEqual(data["created_at"], 123)
        self.assertEqual(data["domain"], "accounts.example.com")
        self.assertEqual(data["enterprise_id"], "enterprise-1")
        self.assertEqual(data["user_info"]["sub"], "sub-id")
        self.assertEqual(data["user_info"]["email"], "alice@example.com")
        self.assertEqual(data["user_info"]["preferred_username"], "alice")
        self.assertEqual(data["user_info"]["nickname"], "AliceNick")
        self.assertEqual(data["user_info"]["session_state"], "session")
        self.assertEqual(data["user_info"]["iss"], "https://accounts.example.com/oauth/sso-enterprise-1")
        self.assertEqual(data["user_info"]["domain"], "accounts.example.com")
        self.assertEqual(data["user_info"]["enterprise_id"], "enterprise-1")

    def test_build_credential_data_prefers_explicit_enterprise_and_domain(self):
        token = jwt_with_payload({
            "sub": "sub-id",
            "preferred_username": "alice",
            "iss": "https://issuer.example.com/auth/sso-derived",
        })

        data = TokenParser.build_credential_data({
            "bearer_token": token,
            "domain": "configured.example.com",
            "enterprise_id": "configured-enterprise",
        })

        self.assertEqual(data["domain"], "configured.example.com")
        self.assertEqual(data["enterprise_id"], "configured-enterprise")
        self.assertEqual(data["user_info"]["domain"], "configured.example.com")
        self.assertEqual(data["user_info"]["enterprise_id"], "configured-enterprise")
        self.assertEqual(data["user_info"]["nickname"], "alice")

    def test_build_credential_data_falls_back_to_stable_anonymous_id_for_invalid_token(self):
        token = "plain-token-12345678"
        data = TokenParser.build_credential_data({
            "bearer_token": token,
            "domain": "www.codebuddy.cn",
        })

        self.assertEqual(data["user_id"], "anonymous_12345678")
        self.assertEqual(data["domain"], "www.codebuddy.cn")
        self.assertEqual(data["bearer_token"], token)

    def test_build_credential_data_omits_none_values(self):
        token = "plain-token-abcdefgh"
        data = TokenParser.build_credential_data({
            "access_token": token,
            "refresh_token": None,
            "scope": None,
        })

        self.assertNotIn("refresh_token", data)
        self.assertNotIn("scope", data)
        self.assertEqual(data["user_id"], "anonymous_abcdefgh")

    def test_extract_user_info_uses_sub_and_stable_anonymous_fallback(self):
        cases = [
            ({}, None),
            ({"preferred_username": "alice"}, None),
            ({"sub": "subject"}, "subject"),
        ]

        for payload, expected in cases:
            with self.subTest(payload=payload):
                token = jwt_with_payload(payload)
                user_id, _ = TokenParser._extract_user_info(token, {})
                self.assertEqual(user_id, expected or f"anonymous_{token[-8:]}")

    def test_extract_user_info_falls_back_to_plain_anonymous_without_token(self):
        user_id, user_info = TokenParser._extract_user_info(None, {})

        self.assertEqual(user_id, "anonymous")
        self.assertEqual(user_info, {})

    def test_extract_user_info_handles_padded_and_malformed_payloads(self):
        padded_payload = base64.urlsafe_b64encode(b'{"sub":"padded"}').decode("ascii")
        padded_token = f"header.{padded_payload}.signature"
        malformed_payload = base64.urlsafe_b64encode(b"not-json").decode("ascii").rstrip("=")

        user_id, _ = TokenParser._extract_user_info(padded_token, {})
        malformed_user_id, malformed_info = TokenParser._extract_user_info(
            f"header.{malformed_payload}.signature",
            {"domain": "fallback"},
        )

        self.assertEqual(user_id, "padded")
        self.assertEqual(malformed_user_id, "anonymous_ignature")
        self.assertEqual(malformed_info, {})

    def test_extract_user_info_handles_unexpected_decoder_error(self):
        with mock.patch(
            "src.codebuddy_oauth.base64.urlsafe_b64decode",
            side_effect=RuntimeError("decoder failed"),
        ):
            user_id, user_info = TokenParser._extract_user_info(
                "header.payload.signature",
                {"domain": "fallback"},
            )

        self.assertEqual(user_id, "anonymous_ignature")
        self.assertEqual(user_info, {})

    def test_extract_issuer_info_ignores_invalid_or_non_sso_issuer(self):
        self.assertEqual(TokenParser._extract_issuer_info("not-a-url"), {})
        self.assertEqual(
            TokenParser._extract_issuer_info("https://issuer.example.com/auth/regular"),
            {"domain": "issuer.example.com"},
        )


class CodeBuddyTokenSaverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = AuthenticatedUser(username="alice", source="session_cookie")

    async def test_saves_sanitized_credential_data(self):
        manager = mock.Mock()
        manager.add_credential_with_data.return_value = True
        manager.get_credentials_info.side_effect = [
            [{"credential_id": "existing-credential"}],
            [
                {"credential_id": "existing-credential"},
                {"credential_id": "new-credential"},
            ],
        ]
        credential_data = {"bearer_token": "token", "user_id": "a/b:c@example.com"}
        schedule_probe = mock.Mock()

        with (
            mock.patch(
                "src.codebuddy_oauth.TokenParser.build_credential_data",
                return_value=credential_data,
            ),
            mock.patch(
                "src.codebuddy_token_manager.get_token_manager_for_user",
                return_value=manager,
            ),
            mock.patch("src.codebuddy_oauth.time.time", return_value=123),
            mock.patch(
                "src.credential_quota.credential_quota_manager.schedule_probe_if_running",
                schedule_probe,
            ),
        ):
            result = await CodeBuddyTokenSaver().save({"access_token": "token"}, self.user)

        self.assertTrue(result)
        manager.add_credential_with_data.assert_called_once_with(
            credential_data=credential_data,
            filename="codebuddy_abcexample.com_123.json",
        )
        schedule_probe.assert_called_once_with(
            "alice",
            manager,
            "new-credential",
        )

    async def test_success_without_new_identifier_does_not_schedule_probe(self):
        manager = mock.Mock()
        manager.add_credential_with_data.return_value = True
        manager.get_credentials_info.return_value = [
            {"credential_id": "existing-credential"},
        ]
        schedule_probe = mock.Mock()
        with (
            mock.patch(
                "src.codebuddy_oauth.TokenParser.build_credential_data",
                return_value={"bearer_token": "token", "user_id": "alice"},
            ),
            mock.patch(
                "src.codebuddy_token_manager.get_token_manager_for_user",
                return_value=manager,
            ),
            mock.patch(
                "src.credential_quota.credential_quota_manager.schedule_probe_if_running",
                schedule_probe,
            ),
        ):
            result = await CodeBuddyTokenSaver().save({"access_token": "token"}, self.user)

        self.assertTrue(result)
        schedule_probe.assert_not_called()

    async def test_returns_false_for_manager_rejection_or_exception(self):
        manager = mock.Mock()
        manager.add_credential_with_data.return_value = False
        manager.get_credentials_info.return_value = []
        with mock.patch(
            "src.codebuddy_token_manager.get_token_manager_for_user",
            return_value=manager,
        ):
            self.assertFalse(await CodeBuddyTokenSaver().save({"access_token": "token"}, self.user))

        with mock.patch(
            "src.codebuddy_oauth.TokenParser.build_credential_data",
            side_effect=RuntimeError("invalid token"),
        ):
            self.assertFalse(await CodeBuddyTokenSaver().save({}, self.user))


class OAuthWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrappers_delegate_to_global_services(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        with (
            mock.patch.object(oauth.auth_state_store, "validate_owner", return_value=True) as validate,
            mock.patch.object(oauth.auth_state_store, "consume", return_value=True) as consume,
            mock.patch.object(
                oauth.codebuddy_auth_client,
                "start_auth",
                new=mock.AsyncMock(return_value={"started": True}),
            ) as start,
            mock.patch.object(
                oauth.codebuddy_auth_client,
                "poll_status",
                new=mock.AsyncMock(return_value={"status": "pending"}),
            ) as poll,
            mock.patch.object(
                oauth.codebuddy_token_saver,
                "save",
                new=mock.AsyncMock(return_value=True),
            ) as save,
        ):
            self.assertTrue(oauth.validate_auth_state_owner("state", user))
            self.assertTrue(oauth.consume_auth_state("state", user))
            self.assertEqual(await oauth.start_codebuddy_auth(), {"started": True})
            self.assertEqual(await oauth.poll_codebuddy_auth_status("state"), {"status": "pending"})
            self.assertTrue(await oauth.save_codebuddy_token({"token": "value"}, user))

        validate.assert_called_once_with("state", user)
        consume.assert_called_once_with("state", user)
        start.assert_awaited_once_with()
        poll.assert_awaited_once_with("state", None)
        save.assert_awaited_once_with({"token": "value"}, user)

    async def test_poll_wrapper_persists_progress_for_owner(self):
        user = AuthenticatedUser(username="alice", source="session_cookie")
        progress = {"access_token": "secret"}
        with (
            mock.patch.object(oauth.auth_state_store, "get_progress", return_value=None) as get_progress,
            mock.patch.object(oauth.auth_state_store, "set_progress", return_value=True) as set_progress,
            mock.patch.object(
                oauth.codebuddy_auth_client,
                "poll_status",
                new=mock.AsyncMock(return_value={"status": "pending", "progress": progress}),
            ) as poll,
        ):
            result = await oauth.poll_codebuddy_auth_status("state", user)

        self.assertEqual(result["progress"], progress)
        get_progress.assert_called_once_with("state", user)
        poll.assert_awaited_once_with("state", None)
        set_progress.assert_called_once_with("state", user, progress)


if __name__ == "__main__":
    unittest.main()
