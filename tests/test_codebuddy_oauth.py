import base64
import json
import unittest
from unittest import mock

from src.auth_types import AuthenticatedUser
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

    def test_auth_state_store_forget_is_idempotent(self):
        store = AuthStateStore()
        user = AuthenticatedUser(username="alice", source="session_cookie")
        store.remember("state", user)

        store.forget("state")
        store.forget("state")

        self.assertFalse(store.validate_owner("state", user))


class CodeBuddyAuthClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_auth_keeps_original_state_when_fresh_retry_has_malformed_data(self):
        class FakeAsyncClient:
            responses = [
                FakeAuthStateResponse({
                    "code": 0,
                    "data": {
                        "state": "state-1",
                        "authUrl": "https://codebuddy.example/auth",
                    },
                }),
                FakeAuthStateResponse({
                    "code": 0,
                    "data": "unexpected",
                }),
            ]

            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def post(self, *_args, **_kwargs):
                return self.responses.pop(0)

        client = CodeBuddyAuthClient()
        client._last_auth_state = "state-1"

        with mock.patch("src.codebuddy_oauth.httpx.AsyncClient", FakeAsyncClient):
            result = await client.start_auth()

        self.assertTrue(result["success"])
        self.assertEqual(result["auth_state"], "state-1")
        self.assertEqual(result["verification_uri_complete"], "https://codebuddy.example/auth")


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
