import base64
import json
import unittest
from unittest import mock

from src.auth_types import AuthenticatedUser
from fastapi import HTTPException

from src.codebuddy_auth_router import poll_for_token, start_device_auth
from src.codebuddy_oauth import AuthStateStore, CodeBuddyAuthClient, TokenParser


def jwt_with_payload(payload):
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{encoded_payload}.signature"


class FakeAuthStateResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


class FakeTokenResponse:
    def __init__(self, payload):
        self.status_code = 200
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
        consume.assert_called_once_with("state", user)
        save_token.assert_awaited_once_with(token_data, user)

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


class TokenParserTests(unittest.TestCase):
    def test_build_credential_data_extracts_user_info_from_jwt_email(self):
        token = jwt_with_payload({
            "sub": "sub-id",
            "email": "alice@example.com",
            "preferred_username": "alice",
            "name": "Alice",
            "sid": "session",
        })

        with mock.patch("src.codebuddy_oauth.time.time", return_value=123):
            data = TokenParser.build_credential_data({
                "access_token": token,
                "expires_in": 3600,
                "refresh_token": "refresh",
                "token_type": "Bearer",
                "scope": "openid",
                "domain": "codebuddy.example",
            })

        self.assertEqual(data["bearer_token"], token)
        self.assertEqual(data["user_id"], "alice@example.com")
        self.assertEqual(data["created_at"], 123)
        self.assertEqual(data["user_info"]["sub"], "sub-id")
        self.assertEqual(data["user_info"]["session_state"], "session")

    def test_build_credential_data_falls_back_to_domain_for_invalid_token(self):
        data = TokenParser.build_credential_data({
            "bearer_token": "not-a-jwt",
            "domain": "www.codebuddy.cn",
        })

        self.assertEqual(data["user_id"], "www.codebuddy.cn")
        self.assertEqual(data["bearer_token"], "not-a-jwt")

    def test_build_credential_data_omits_none_values(self):
        data = TokenParser.build_credential_data({
            "access_token": "not-a-jwt",
            "refresh_token": None,
            "scope": None,
        })

        self.assertNotIn("refresh_token", data)
        self.assertNotIn("scope", data)
        self.assertEqual(data["user_id"], "unknown")


if __name__ == "__main__":
    unittest.main()
