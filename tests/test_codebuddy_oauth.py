import base64
import json
import unittest
from unittest import mock

from src.auth_types import AuthenticatedUser
from fastapi import HTTPException

import src.codebuddy_oauth as oauth
from src.codebuddy_auth_router import cancel_auth, oauth_callback, poll_for_token, start_device_auth
from src.codebuddy_oauth import AuthStateStore, CodeBuddyAuthClient, CodeBuddyTokenSaver, TokenParser


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
    def test_auth_state_store_validates_same_owner_and_rejects_other_user(self):
        store = AuthStateStore(ttl_seconds=60)
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")

        store.remember("state", owner)

        self.assertTrue(store.validate_owner("state", owner))
        self.assertFalse(store.validate_owner("state", other))
        self.assertFalse(store.validate_owner("missing", owner))

    def test_auth_state_store_expires_only_after_ttl_boundary(self):
        store = AuthStateStore(ttl_seconds=60)
        user = AuthenticatedUser(username="alice", source="session_cookie")

        with mock.patch("src.codebuddy_oauth.time.time", return_value=100):
            store.remember("state", user)

        with mock.patch("src.codebuddy_oauth.time.time", return_value=160):
            self.assertTrue(store.validate_owner("state", user))

        with mock.patch("src.codebuddy_oauth.time.time", return_value=161):
            self.assertFalse(store.validate_owner("state", user))

    def test_consumed_auth_state_cannot_be_polled_or_registered_again(self):
        store = AuthStateStore()
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")
        store.remember("state", owner)

        self.assertTrue(store.consume("state", owner))

        self.assertFalse(store.validate_owner("state", owner))
        self.assertFalse(store.remember("state", other))
        self.assertFalse(store.consume("state", owner))

    def test_auth_state_can_only_be_consumed_by_its_owner(self):
        store = AuthStateStore()
        owner = AuthenticatedUser(username="alice", source="session_cookie")
        other = AuthenticatedUser(username="bob", source="session_cookie")
        store.remember("state", owner)

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
            store.remember("state", owner)
        with mock.patch("src.codebuddy_oauth.time.time", return_value=120):
            self.assertTrue(store.consume("state", owner))
        with mock.patch("src.codebuddy_oauth.time.time", return_value=180):
            self.assertFalse(store.remember("state", other))
        with mock.patch("src.codebuddy_oauth.time.time", return_value=181):
            self.assertTrue(store.remember("state", other))

    def test_auth_state_store_rejects_duplicate_without_overwriting_owner(self):
        store = AuthStateStore()
        first_owner = AuthenticatedUser(username="alice", source="session_cookie")
        second_owner = AuthenticatedUser(username="bob", source="session_cookie")

        self.assertTrue(store.remember("state", first_owner))
        self.assertFalse(store.remember("state", second_owner))

        self.assertTrue(store.validate_owner("state", first_owner))
        self.assertFalse(store.validate_owner("state", second_owner))


class CodeBuddyAuthClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_http_clients_ignore_environment_proxy_settings(self):
        created_kwargs = []

        class FakeAsyncClient:
            responses = [
                FakeAuthStateResponse({
                    "code": 0,
                    "data": {"state": "state-1", "authUrl": "https://codebuddy.example/auth"},
                }),
                FakeTokenResponse({"code": 11217, "msg": "login ing..."}),
            ]

            def __init__(self, **kwargs):
                created_kwargs.append(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def post(self, *_args, **_kwargs):
                return self.responses.pop(0)

            async def get(self, *_args, **_kwargs):
                return self.responses.pop(0)

        client = CodeBuddyAuthClient()

        with mock.patch("src.codebuddy_oauth.httpx.AsyncClient", FakeAsyncClient):
            await client.start_auth()
            await client.poll_status("state-1")

        self.assertEqual(len(created_kwargs), 2)
        for kwargs in created_kwargs:
            self.assertIs(kwargs.get("trust_env"), False)

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

        client = CodeBuddyAuthClient()

        with mock.patch("src.codebuddy_oauth.httpx.AsyncClient", FakeAsyncClient):
            first_result = await client.start_auth()
            second_result = await client.start_auth()

        self.assertTrue(first_result["success"])
        self.assertTrue(second_result["success"])
        self.assertEqual(first_result["auth_state"], "state-1")
        self.assertEqual(second_result["auth_state"], "state-1")
        self.assertEqual(post_count, 2)

    async def test_start_auth_reports_incomplete_upstream_response(self):
        client = CodeBuddyAuthClient()

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
        class BrokenAsyncClient:
            def __init__(self, **_kwargs):
                raise RuntimeError("敏感的认证客户端异常详情")

        with mock.patch("src.codebuddy_oauth.httpx.AsyncClient", BrokenAsyncClient):
            result = await CodeBuddyAuthClient().start_auth()

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "认证启动失败，请稍后重试")
        self.assertNotIn("敏感的认证客户端异常详情", result["message"])

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
                FakeTokenResponse({
                    "code": 0,
                    "data": {
                        "accessToken": "token",
                        "tokenType": "Custom",
                        "expiresIn": 10,
                        "refreshToken": "refresh",
                        "sessionState": "session",
                        "scope": "openid",
                        "domain": "codebuddy.example",
                        "enterpriseId": "enterprise-1",
                    },
                }),
                "success",
            ),
            (
                FakeTokenResponse({"code": 0, "data": {}}),
                "unknown",
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
                factory = lambda **kwargs: FakeAsyncClient(response, **kwargs)
                with mock.patch("src.codebuddy_oauth.httpx.AsyncClient", factory):
                    result = await CodeBuddyAuthClient().poll_status("state")

                self.assertEqual(result["status"], expected_status)
                if expected_status == "success":
                    self.assertEqual(result["token_data"]["access_token"], "token")
                    self.assertEqual(result["token_data"]["token_type"], "Custom")
                    self.assertEqual(result["token_data"]["enterprise_id"], "enterprise-1")

    async def test_poll_status_maps_client_exception(self):
        class BrokenAsyncClient:
            def __init__(self, **_kwargs):
                raise RuntimeError("network unavailable")

        with mock.patch("src.codebuddy_oauth.httpx.AsyncClient", BrokenAsyncClient):
            result = await CodeBuddyAuthClient().poll_status("state")

        self.assertEqual(result["status"], "error")
        self.assertIn("network unavailable", result["message"])


class CodeBuddyAuthRouterTests(unittest.IsolatedAsyncioTestCase):
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
            mock.patch("src.codebuddy_auth_router.remember_auth_state", return_value=True) as remember,
        ):
            result = await start_device_auth(user)

        self.assertEqual(result, upstream_result)
        remember.assert_called_once_with("new-state", user)

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
            mock.patch("src.codebuddy_auth_router.remember_auth_state", return_value=False),
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
            mock.patch("src.codebuddy_auth_router.remember_auth_state") as remember,
        ):
            result = await start_device_auth(user)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "invalid_auth_state")
        remember.assert_not_called()

    async def test_start_device_auth_returns_upstream_failure(self):
        upstream_result = {"success": False, "error": "unavailable"}
        with mock.patch(
            "src.codebuddy_auth_router.start_codebuddy_auth",
            new=mock.AsyncMock(return_value=upstream_result),
        ):
            result = await start_device_auth(
                AuthenticatedUser(username="alice", source="session_cookie")
            )

        self.assertIs(result, upstream_result)

    async def test_start_device_auth_maps_unexpected_exception(self):
        with mock.patch(
            "src.codebuddy_auth_router.start_codebuddy_auth",
            new=mock.AsyncMock(side_effect=RuntimeError("敏感的认证路由异常详情")),
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
            ({"status": "error"}, "auth_error"),
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

                self.assertEqual(response.status_code, 400)
                self.assertEqual(json.loads(response.body)["error"], expected_error)

    async def test_oauth_callback_reports_success_or_error(self):
        error_response = await oauth_callback(error="access_denied")
        success_response = await oauth_callback(code="code", state="state")

        self.assertEqual(error_response.status_code, 400)
        self.assertEqual(json.loads(error_response.body)["error"], "access_denied")
        self.assertEqual(json.loads(success_response.body)["code"], "code")


class TokenParserTests(unittest.TestCase):
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
        credential_data = {"bearer_token": "token", "user_id": "a/b:c@example.com"}

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
        ):
            result = await CodeBuddyTokenSaver().save({"access_token": "token"}, self.user)

        self.assertTrue(result)
        manager.add_credential_with_data.assert_called_once_with(
            credential_data=credential_data,
            filename="codebuddy_abcexample.com_123.json",
        )

    async def test_returns_false_for_manager_rejection_or_exception(self):
        manager = mock.Mock()
        manager.add_credential_with_data.return_value = False
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
            mock.patch.object(oauth.auth_state_store, "remember", return_value=True) as remember,
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
            self.assertTrue(oauth.remember_auth_state("state", user))
            self.assertTrue(oauth.validate_auth_state_owner("state", user))
            self.assertTrue(oauth.consume_auth_state("state", user))
            self.assertEqual(await oauth.start_codebuddy_auth(), {"started": True})
            self.assertEqual(await oauth.poll_codebuddy_auth_status("state"), {"status": "pending"})
            self.assertTrue(await oauth.save_codebuddy_token({"token": "value"}, user))

        remember.assert_called_once_with("state", user)
        validate.assert_called_once_with("state", user)
        consume.assert_called_once_with("state", user)
        start.assert_awaited_once_with()
        poll.assert_awaited_once_with("state")
        save.assert_awaited_once_with({"token": "value"}, user)


if __name__ == "__main__":
    unittest.main()
