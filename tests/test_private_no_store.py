import unittest
from unittest import mock

import config
import httpx
from fastapi.responses import StreamingResponse

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.private_response import (
    PrivateNoStoreMiddleware,
    SecurityResponseHeadersMiddleware,
    _is_secure_scope,
)
from src.session_store import session_store
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class PrivateNoStoreResponseTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
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
        base_url="http://localhost",
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
        async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
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

    async def test_successful_session_auth_refreshes_cookie_on_success_error_and_redirect(self):
        responses = [
            await self._request("GET", "/api/admin/api-keys", session=True),
            await self._request("DELETE", "/api/admin/api-keys/missing", session=True),
            await self._request("GET", "/api/admin/api-keys/", session=True),
        ]

        self.assertEqual([response.status_code for response in responses], [200, 404, 307])
        for response in responses:
            with self.subTest(status=response.status_code):
                cookie = response.headers["Set-Cookie"]
                self.assertIn(f"{SESSION_COOKIE_NAME}={self.session_id}", cookie)
                self.assertIn("Max-Age=604800", cookie)
                self.assertIn("HttpOnly", cookie)
                self.assertIn("SameSite=lax", cookie)

    async def test_session_cookie_refresh_covers_streaming_and_trusted_https_scope(self):
        async def chunks():
            yield b"data: [DONE]\n\n"

        stream = StreamingResponse(chunks(), media_type="text/event-stream")
        with mock.patch(
            "src.openai_router.chat_completions",
            new=mock.AsyncMock(return_value=stream),
        ):
            response = await self._request(
                "POST",
                "/api/admin/playground/openai/v1/chat/completions",
                session=True,
                json={"messages": [{"role": "user", "content": "hello"}], "stream": True},
                base_url="https://localhost",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Max-Age=604800", response.headers["Set-Cookie"])
        self.assertIn("Secure", response.headers["Set-Cookie"])

    async def test_invalid_session_does_not_refresh_cookie(self):
        response = await self._request(
            "GET",
            "/api/admin/api-keys",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}=invalid"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Set-Cookie", response.headers)

    async def test_private_method_not_allowed_response_is_private_no_store(self):
        response = await self._request(
            "POST",
            "/openai/v1/models",
            api_key=True,
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_public_sensitive_routes_mark_validation_and_logout_responses(self):
        invalid_login = await self._request("POST", "/auth/login", json={})
        logout = await self._request("POST", "/auth/logout", session=True)

        self.assertEqual(invalid_login.status_code, 422)
        self.assertEqual(invalid_login.headers["Cache-Control"], "private, no-store")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.headers["Cache-Control"], "private, no-store")

    async def test_login_body_over_8_kib_is_rejected_before_validation(self):
        response = await self._request(
            "POST",
            "/auth/login",
            content=b"x" * (8 * 1024 + 1),
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_removed_oauth_callback_returns_natural_404_without_echoing_parameters(self):
        callback = await self._request(
            "GET",
            "/codebuddy/auth/callback?code=sensitive-code&state=sensitive-state",
        )

        self.assertEqual(callback.status_code, 404)
        self.assertNotIn("sensitive-code", callback.text)
        self.assertNotIn("sensitive-state", callback.text)

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
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("Max-Age=604800", response.headers["Set-Cookie"])

    async def test_public_health_response_is_not_forced_to_no_store(self):
        response = await self._request("GET", "/health")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Cache-Control", response.headers)

    async def test_all_http_responses_receive_browser_security_headers(self):
        responses = (
            await self._request("GET", "/health"),
            await self._request("GET", "/missing"),
        )

        for response in responses:
            with self.subTest(status=response.status_code):
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
                policy = response.headers["Content-Security-Policy"]
                self.assertIn("frame-ancestors 'none'", policy)
                self.assertIn("script-src 'self'", policy)
                self.assertNotIn("'unsafe-eval'", policy)
                self.assertNotIn("script-src 'self' 'unsafe-inline'", policy)

    async def test_documentation_csp_allows_only_required_remote_assets(self):
        for path in ("/docs", "/redoc"):
            with self.subTest(path=path):
                response = await self._request("GET", path)
                policy = response.headers["Content-Security-Policy"]
                self.assertIn(
                    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
                    policy,
                )
                self.assertIn(
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com",
                    policy,
                )
                self.assertIn("font-src 'self' https://fonts.gstatic.com", policy)
                self.assertIn(
                    "img-src 'self' data: https://fastapi.tiangolo.com",
                    policy,
                )

        schema = await self._request("GET", "/openapi.json")
        self.assertNotIn(
            "cdn.jsdelivr.net",
            schema.headers["Content-Security-Policy"],
        )

    async def test_only_private_slash_redirect_is_private_no_store(self):
        private_redirect = await self._request(
            "POST",
            "/codebuddy/auth/poll/",
            session=True,
            json={"auth_state": "state"},
        )
        public_redirect = await self._request("GET", "/health/")

        self.assertEqual(private_redirect.status_code, 307)
        self.assertEqual(private_redirect.headers["Location"], "http://localhost/codebuddy/auth/poll")
        self.assertEqual(
            private_redirect.headers["Cache-Control"],
            "private, no-store",
        )
        self.assertEqual(public_redirect.status_code, 307)
        self.assertNotIn("Cache-Control", public_redirect.headers)

        no_cookie_redirect = await self._request(
            "POST",
            "/codebuddy/auth/poll/",
            headers={"Cookie": "other=value; malformed"},
            json={"auth_state": "state"},
        )
        invalid_cookie_redirect = await self._request(
            "POST",
            "/codebuddy/auth/poll/",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}=invalid"},
            json={"auth_state": "state"},
        )
        self.assertNotIn("Set-Cookie", no_cookie_redirect.headers)
        self.assertNotIn("Set-Cookie", invalid_cookie_redirect.headers)


class PrivateNoStoreMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def test_secure_scope_recognizes_direct_https_and_plain_http(self):
        self.assertTrue(_is_secure_scope({"type": "http", "scheme": "https", "headers": []}))
        self.assertFalse(_is_secure_scope({"type": "http", "scheme": "http", "headers": []}))
        self.assertFalse(_is_secure_scope({
            "type": "http",
            "scheme": "http",
            "headers": [(b"x-forwarded-proto", b"https")],
        }))

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


class SecurityResponseHeadersMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_configurable_csp_ancestors_do_not_change_fixed_x_frame_options(self):
        messages = []

        async def downstream(_scope, _receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"x-frame-options", b"ALLOWALL"),
                    (b"content-security-policy", b"default-src *"),
                ],
            })
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        middleware = SecurityResponseHeadersMiddleware(
            downstream,
            "'self' https://portal.example.com",
        )
        await middleware(
            {"type": "http", "scheme": "http", "headers": []},
            receive,
            send,
        )

        headers = httpx.Headers(messages[0]["headers"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn(
            "frame-ancestors 'self' https://portal.example.com",
            headers["Content-Security-Policy"],
        )

    async def test_non_http_scope_passes_through_unchanged(self):
        received_scopes = []

        async def downstream(scope, _receive, _send):
            received_scopes.append(scope)

        async def receive():
            return {"type": "lifespan.startup"}

        middleware = SecurityResponseHeadersMiddleware(downstream, "'none'")
        scope = {"type": "lifespan"}
        await middleware(scope, receive, mock.AsyncMock())

        self.assertEqual(received_scopes, [scope])


if __name__ == "__main__":
    unittest.main()
