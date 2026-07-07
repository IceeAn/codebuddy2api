import unittest
from unittest import mock

import config
import httpx
from fastapi.responses import StreamingResponse

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.private_response import PrivateNoStoreMiddleware
from src.session_store import session_store
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class PrivateNoStoreResponseTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        config._config_cache["CODEBUDDY_CREDS_DIR"] = str(self.temp_path / "creds")
        self.api_key = api_key_store.create_key("admin", "cache-test")["api_key"]
        self.session_id = session_store.create("admin")

    async def _request(
        self,
        method,
        path,
        *,
        api_key=False,
        session=False,
        json=None,
        content=None,
        headers=None,
        raise_app_exceptions=True,
    ):
        request_headers = dict(headers or {})
        if api_key:
            request_headers["Authorization"] = f"Bearer {self.api_key}"
        if session:
            request_headers["Cookie"] = f"{SESSION_COOKIE_NAME}={self.session_id}"
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.request(
                method,
                path,
                headers=request_headers,
                json=json,
                content=content,
            )

    async def test_streaming_response_overrides_upstream_cache_control(self):
        async def chunks():
            yield b"data: [DONE]\n\n"

        stream = StreamingResponse(
            chunks(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
        with mock.patch(
            "src.openai_router.chat_completions",
            new=mock.AsyncMock(return_value=stream),
        ):
            response = await self._request(
                "POST",
                "/openai/v1/chat/completions",
                api_key=True,
                json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_authenticated_error_responses_are_private_no_store(self):
        cases = [
            ("GET", "/openai/v1/models", {}, None, 401),
            ("DELETE", "/api/admin/api-keys/missing", {"session": True}, None, 404),
            (
                "POST",
                "/api/admin/credentials",
                {"session": True},
                {"bearer_token": " "},
                422,
            ),
            ("POST", "/codebuddy/auth/poll", {"session": True}, {}, 400),
            ("POST", "/codebuddy/auth/cancel", {"session": True}, {}, 400),
        ]

        for method, path, auth, body, expected_status in cases:
            with self.subTest(path=path, expected_status=expected_status):
                response = await self._request(method, path, json=body, **auth)
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_private_method_not_allowed_response_is_private_no_store(self):
        response = await self._request(
            "POST",
            "/openai/v1/models",
            api_key=True,
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_public_sensitive_routes_mark_validation_and_callback_responses(self):
        invalid_login = await self._request("POST", "/auth/login", json={})
        logout = await self._request("POST", "/auth/logout", session=True)
        callback = await self._request("GET", "/codebuddy/auth/callback?code=code")

        self.assertEqual(invalid_login.status_code, 422)
        self.assertEqual(invalid_login.headers["Cache-Control"], "private, no-store")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.headers["Cache-Control"], "private, no-store")
        self.assertEqual(callback.status_code, 200)
        self.assertEqual(callback.headers["Cache-Control"], "private, no-store")

    async def test_malformed_json_responses_are_private_no_store(self):
        cases = [
            ("/auth/login", False),
            ("/api/admin/credentials", True),
        ]

        for path, session in cases:
            with self.subTest(path=path):
                response = await self._request(
                    "POST",
                    path,
                    session=session,
                    content="{",
                    headers={"Content-Type": "application/json"},
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_unhandled_private_route_error_is_private_no_store(self):
        with mock.patch(
            "src.admin_router.api_key_store.list_keys",
            side_effect=RuntimeError("storage failed"),
        ):
            response = await self._request(
                "GET",
                "/api/admin/api-keys",
                session=True,
                raise_app_exceptions=False,
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_public_health_response_is_not_forced_to_no_store(self):
        response = await self._request("GET", "/health")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cache-Control", response.headers)

    async def test_only_private_slash_redirect_is_private_no_store(self):
        private_redirect = await self._request(
            "GET",
            "/codebuddy/auth/callback/?code=secret&state=s1",
        )
        public_redirect = await self._request("GET", "/health/")

        self.assertEqual(private_redirect.status_code, 307)
        self.assertIn("code=secret&state=s1", private_redirect.headers["Location"])
        self.assertEqual(
            private_redirect.headers["Cache-Control"],
            "private, no-store",
        )
        self.assertEqual(public_redirect.status_code, 307)
        self.assertNotIn("Cache-Control", public_redirect.headers)


class PrivateNoStoreMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_http_scope_passes_through_unchanged(self):
        received_scopes = []

        async def downstream(scope, _receive, _send):
            received_scopes.append(scope)

        async def receive():
            return {"type": "lifespan.startup"}

        async def send(_message):
            return None

        scope = {"type": "lifespan"}
        middleware = PrivateNoStoreMiddleware(downstream)

        await middleware(scope, receive, send)

        self.assertEqual(received_scopes, [scope])


if __name__ == "__main__":
    unittest.main()
