import base64
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import config
from fastapi import HTTPException, Response
from starlette.requests import Request

from src.auth import (
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    LoginRequest,
    UsersFileStore,
    authenticate,
    login as service_login,
    logout as service_logout,
)
from src.codebuddy_api_client import codebuddy_api_client
from src.codebuddy_auth_router import get_auth_poll_headers, get_auth_start_headers, get_codebuddy_auth_state_endpoint
from src.codebuddy_router import CodeBuddyStreamService, RequestProcessor, list_v1_models
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
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

        self.assertEqual(config.get_codebuddy_api_endpoint(), "https://copilot.tencent.com")

    def test_codebuddy_headers_follow_configured_china_endpoint(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://copilot.tencent.com"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

        headers = codebuddy_api_client.generate_codebuddy_headers("token-value", "user-id")

        self.assertEqual(headers["Host"], "copilot.tencent.com")
        self.assertEqual(headers["X-Domain"], "copilot.tencent.com")

    def test_codebuddy_headers_use_credential_domain_when_available(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://copilot.tencent.com"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

        headers = codebuddy_api_client.generate_codebuddy_headers(
            "token-value",
            "user-id",
            domain="www.codebuddy.cn",
        )

        self.assertEqual(headers["Host"], "copilot.tencent.com")
        self.assertEqual(headers["X-Domain"], "www.codebuddy.cn")

    def test_codebuddy_headers_reject_unsafe_credential_domain(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://copilot.tencent.com"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

        headers = codebuddy_api_client.generate_codebuddy_headers(
            "token-value",
            "user-id",
            domain="www.codebuddy.cn\r\nX-Evil: true",
        )

        self.assertEqual(headers["X-Domain"], "copilot.tencent.com")

    def test_codebuddy_auth_endpoint_follows_configured_china_endpoint(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://copilot.tencent.com"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

        self.assertEqual(
            get_codebuddy_auth_state_endpoint(),
            "https://copilot.tencent.com/v2/plugin/auth/state",
        )
        self.assertEqual(get_auth_start_headers()["Host"], "copilot.tencent.com")
        self.assertEqual(get_auth_poll_headers()["X-Domain"], "copilot.tencent.com")

    def test_default_models_follow_china_endpoint(self):
        config._config_cache["CODEBUDDY_MODELS"] = ",".join(config.DEFAULT_CODEBUDDY_MODELS)

        models = config.get_available_models()

        self.assertEqual(models[0], "glm-5.1")
        self.assertIn("deepseek-v4-pro", models)
        self.assertNotIn("auto-chat", models)

    def test_prepare_payload_uses_first_configured_model_when_missing(self):
        config._config_cache["CODEBUDDY_MODELS"] = ",".join(config.DEFAULT_CODEBUDDY_MODELS)

        payload = RequestProcessor.prepare_payload({
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["model"], "glm-5.1")

    def test_prepare_payload_forces_deepseek_v4_reasoning(self):
        payload = RequestProcessor.prepare_payload({
            "model": "deepseek-v4-pro",
            "reasoning_effort": "low",
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled"})

    def test_prepare_payload_forces_namespaced_deepseek_v4_reasoning(self):
        payload = RequestProcessor.prepare_payload({
            "model": "codebuddy/deepseek-v4-flash",
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled"})

    def test_prepare_payload_forces_glm_5_1_reasoning(self):
        payload = RequestProcessor.prepare_payload({
            "model": "glm-5.1",
            "reasoning_effort": "low",
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled", "clear_thinking": False})

    def test_prepare_payload_does_not_force_reasoning_for_other_models(self):
        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "reasoning_effort": "low",
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def _request(self, authorization: str) -> Request:
        return Request({
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"authorization", authorization.encode("utf-8"))],
        })


class AuthSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_config = config._config_cache.copy()
        self._temp_dir = tempfile.TemporaryDirectory()
        users_file = Path(self._temp_dir.name) / "users.txt"
        users_file.write_text(
            f"admin:{create_password_hash('secret-password')}\n",
            encoding="utf-8",
        )
        config._config_cache["CODEBUDDY_USERS_FILE"] = str(users_file)

    def tearDown(self):
        config._config_cache = self._original_config.copy()
        self._temp_dir.cleanup()

    def _request(self, cookie: str = "") -> Request:
        headers = []
        if cookie:
            headers.append((b"cookie", cookie.encode("utf-8")))
        return Request({
            "type": "http",
            "method": "GET",
            "path": "/",
            "scheme": "http",
            "headers": headers,
        })

    async def _login_and_get_cookie(self) -> str:
        response = Response()
        result = await service_login(
            self._request(),
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

        user = authenticate(self._request(cookie))

        self.assertEqual(user.username, "admin")
        self.assertEqual(user.source, "session_cookie")

    async def test_logout_invalidates_session_cookie(self):
        cookie = await self._login_and_get_cookie()
        response = Response()

        result = await service_logout(self._request(cookie), response)

        self.assertFalse(result["authenticated"])
        with self.assertRaises(HTTPException) as context:
            authenticate(self._request(cookie))
        self.assertEqual(context.exception.status_code, 401)


class _FakeStreamResponse:
    def __init__(self, chunks):
        self.status_code = 200
        self._chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_text(self, chunk_size=None):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return b""


class _FakeHttpClient:
    def __init__(self, chunks):
        self._chunks = chunks

    def stream(self, *args, **kwargs):
        return _FakeStreamResponse(self._chunks)


class StreamingFormatTests(unittest.IsolatedAsyncioTestCase):
    async def _render_stream_body(self, chunks, model="glm-5.1"):
        async def fake_get_http_client():
            return _FakeHttpClient(chunks)

        with mock.patch("src.codebuddy_router.get_http_client", fake_get_http_client):
            response = await CodeBuddyStreamService().handle_stream_response(
                {"model": model},
                {},
            )
            body_parts = []
            async for part in response.body_iterator:
                body_parts.append(part.decode("utf-8") if isinstance(part, bytes) else part)

        return "".join(body_parts)

    def _stream_payloads(self, body):
        events = [event for event in body.split("\n\n") if event]
        return [
            json.loads(event[6:])
            for event in events
            if event.startswith("data: ") and event != "data: [DONE]"
        ]

    async def test_model_list_returns_minimal_openai_model_objects(self):
        with mock.patch("src.codebuddy_router.get_available_models_list", lambda: ["deepseek-v4-pro", "glm-5.1"]):
            response = await list_v1_models(AuthenticatedUser(username="admin", source="users_file"))

        models = {item["id"]: item for item in response["data"]}

        self.assertEqual(models["deepseek-v4-pro"]["object"], "model")
        self.assertEqual(models["deepseek-v4-pro"]["owned_by"], "codebuddy")
        self.assertIsInstance(models["deepseek-v4-pro"]["created"], int)
        self.assertNotIn("reasoning", models["deepseek-v4-pro"])
        self.assertNotIn("limit", models["glm-5.1"])

    async def test_stream_response_uses_openai_sse_event_boundaries(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        events = [event for event in body.split("\n\n") if event]

        self.assertEqual(len(events), 3)
        self.assertEqual(events[2], "data: [DONE]")
        self.assertTrue(events[0].startswith("data: "))

        payload = json.loads(events[0][6:])
        self.assertEqual(payload["object"], "chat.completion.chunk")
        self.assertEqual(payload["model"], "glm-5.1")
        self.assertIn("id", payload)
        self.assertIn("created", payload)
        self.assertEqual(payload["choices"][0]["delta"], {"role": "assistant"})

        content_payload = json.loads(events[1][6:])
        self.assertEqual(content_payload["id"], payload["id"])
        self.assertEqual(content_payload["created"], payload["created"])
        self.assertEqual(content_payload["choices"][0]["delta"]["content"], "hi")

    async def test_stream_response_normalizes_reasoning_chunks_for_opencode(self):
        chunks = [
            'data: {"id":"upstream-1","created":1,"model":"wrong","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"我","content":""},"finish_reason":null}]}\n'
            'data: {"id":"upstream-2","created":2,"model":"wrong","choices":[{"index":0,"delta":{"role":"","reasoning_content":"需要","content":null},"finish_reason":null}]}\n'
            'data: {"id":"upstream-3","created":3,"model":"wrong","choices":[{"index":0,"delta":{"role":"","reasoning_content":"先","content":""},"finish_reason":null}]}\n'
            'data: {"id":"upstream-4","created":4,"model":"wrong","choices":[{"index":0,"delta":{"role":"","content":"结论"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)

        self.assertEqual([payload["choices"][0]["delta"] for payload in payloads], [
            {"role": "assistant"},
            {"reasoning_content": "我"},
            {"reasoning_content": "需要"},
            {"reasoning_content": "先"},
            {"content": "结论"},
        ])
        self.assertEqual(len({payload["id"] for payload in payloads}), 1)
        self.assertEqual(len({payload["created"] for payload in payloads}), 1)
        self.assertEqual({payload["model"] for payload in payloads}, {"glm-5.1"})

    async def test_stream_response_splits_mixed_reasoning_and_content_delta(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"先想","content":"再答"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)

        self.assertEqual([payload["choices"][0]["delta"] for payload in payloads], [
            {"role": "assistant"},
            {"reasoning_content": "先想"},
            {"content": "再答"},
        ])


if __name__ == "__main__":
    unittest.main()
