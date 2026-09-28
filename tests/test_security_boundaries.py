"""浏览器来源、并发和敏感信息边界的回归测试。"""
import tests

import asyncio
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI, Request

import config
from src.http_security import AdmissionMiddleware, ExternalCORSMiddleware, SessionOriginMiddleware, normalize_origin
from src.request_limits import RequestBodyLimitMiddleware
from src.sse import SSEDataError, iter_sse_events
from src.stream_service import CodeBuddyStreamService, UpstreamAPIError, _ManagedStreamingResponse
from tests.helpers import ConfigIsolationMixin, async_chunks, make_request


class OriginTests(unittest.IsolatedAsyncioTestCase):
    def app(self, public_origin=""):
        app = FastAPI()

        @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
        async def endpoint(path: str):
            return {"accepted": True}

        app.add_middleware(SessionOriginMiddleware, public_origin=public_origin)
        return app

    async def request(self, path="/auth/logout", headers=None, method="POST", public_origin=""):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app(public_origin)), base_url="https://localhost") as client:
            return await client.request(method, path, headers=headers)

    async def test_unsafe_cookie_routes_require_exact_origin(self):
        for path in ("/auth/login", "/auth/logout", "/codebuddy/auth/start", "/api/admin/credentials/rotation/toggle"):
            for origin in (None, "null", "https://localhost:9443", "https://evil.example", "https://localhost/extra"):
                headers = {"Origin": origin} if origin is not None else {}
                with self.subTest(path=path, origin=origin):
                    response = await self.request(path, headers)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual((await self.request(headers={"Origin":"https://localhost:443"})).status_code, 200)

    async def test_referer_fallback_and_fetch_metadata(self):
        self.assertEqual((await self.request(headers={"Referer":"https://localhost/settings?a=1"})).status_code, 200)
        for headers in ({"Referer":"https://elsewhere.test/"}, {"Origin":"null", "Referer":"https://localhost/"}, {"Origin":"https://localhost", "Sec-Fetch-Site":"same-site"}, {"Referer":"broken"}):
            self.assertEqual((await self.request(headers=headers)).status_code, 403)
        self.assertEqual((await self.request(headers={"Origin":"https://localhost", "Sec-Fetch-Site":"same-origin"})).status_code, 200)

    async def test_public_origin_is_authoritative(self):
        self.assertEqual((await self.request(headers={"Origin":"https://public.example"}, public_origin="https://public.example")).status_code, 200)
        self.assertEqual((await self.request(headers={"Origin":"https://localhost"}, public_origin="https://public.example")).status_code, 403)

    async def test_safe_methods_and_external_protocols_are_unaffected(self):
        for method in ("GET", "OPTIONS"):
            self.assertEqual((await self.request(method=method)).status_code, 200)
        for path in ("/openai/v1/responses", "/anthropic/v1/messages", "/authentic"):
            self.assertEqual((await self.request(path)).status_code, 200)

    async def test_playground_requires_json_but_external_api_does_not(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""):
            headers = {"Origin":"https://localhost", "Content-Type":content_type}
            response = await self.request("/api/admin/playground/openai/v1/responses", headers)
            self.assertEqual(response.status_code, 415)
        headers = {"Origin":"https://localhost", "Content-Type":"application/json; charset=utf-8"}
        self.assertEqual((await self.request("/api/admin/playground/openai/v1/responses", headers)).status_code, 200)

    def test_origin_normalization_rejects_ambiguous_input(self):
        self.assertEqual(normalize_origin("https://EXAMPLE.com:443"), "https://example.com")
        self.assertEqual(normalize_origin("http://[::1]:8001"), "http://[::1]:8001")
        self.assertEqual(normalize_origin("https://例子.测试"), "https://xn--fsqu00a.xn--0zwm56d")
        for value in ("null", "https://a:bad", "https://a/path", "https://u:p@a", "https://a?x=1", "https://a#x", "https://a\n", "https://a\\b", "https://*.example", "https://[bad]", "https://[fe80::1%eth0]", "https://-a", "ftp://a"):
            self.assertIsNone(normalize_origin(value), value)

    async def test_cors_never_grants_management_access(self):
        app = ExternalCORSMiddleware(self.app(), allow_origins=["https://client.example"], allow_methods=["*"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://localhost") as client:
            for path in ("/auth/login", "/api/admin/settings", "/codebuddy/auth/start", "/openai/v1/responses", "/anthropic/v1/messages"):
                response = await client.options(path, headers={"Origin":"https://client.example", "Access-Control-Request-Method":"POST"})
                self.assertEqual("access-control-allow-origin" in response.headers, path.startswith(("/openai/", "/anthropic/")))

    async def test_body_deadline_returns_408_and_cancels_receive(self):
        app = FastAPI()
        @app.post("/echo")
        async def endpoint(request: Request):
            return await request.body()
        app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=1024, login_max_body_bytes=128, body_timeout=0.01)
        closed = asyncio.Event()
        async def slow_body():
            try:
                yield b"x"
                await asyncio.Event().wait()
            finally:
                closed.set()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/echo", content=slow_body())
        self.assertEqual(response.status_code, 408)
        self.assertTrue(closed.is_set())


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_is_held_until_response_ends_and_released_on_failure(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def app(scope, receive, send):
            entered.set()
            await release.wait()
            raise RuntimeError("受控故障")

        middleware = AdmissionMiddleware(app, limit=1)
        scope = {"type":"http", "path":"/openai/v1/responses", "headers":[]}
        send = mock.AsyncMock()
        task = asyncio.create_task(middleware(scope, mock.AsyncMock(), send))
        await entered.wait()
        try:
            for path, status in (
                ("/openai/v1/responses", 503),
                ("/api/admin/settings", 503),
                ("/anthropic/v1/messages", 529),
                ("/api/admin/playground/anthropic/v1/messages", 529),
            ):
                with self.subTest(path=path):
                    rejected_send = mock.AsyncMock()
                    await middleware({**scope, "path": path}, mock.AsyncMock(), rejected_send)
                    start, body = [call.args[0] for call in rejected_send.call_args_list]
                    self.assertEqual(start["status"], status)
                    self.assertEqual(middleware.active, 1)
                    if status == 529:
                        content = json.loads(body["body"])
                        headers = dict(start["headers"])
                        self.assertEqual(content["type"], "error")
                        self.assertEqual(content["error"]["type"], "overloaded_error")
                        self.assertEqual(content["request_id"], headers[b"request-id"].decode())
                        self.assertEqual(headers[b"cache-control"], b"private, no-store")
        finally:
            release.set()
            with self.assertRaises(RuntimeError):
                await task
        self.assertEqual(middleware.active, 0)

    async def test_non_http_and_unlimited_pass_through(self):
        for scope, limit in (({"type":"lifespan"}, 1), ({"type":"http"}, None)):
            app = mock.AsyncMock()
            await AdmissionMiddleware(app, limit=limit)(scope, mock.AsyncMock(), mock.AsyncMock())
            app.assert_awaited_once()


class StreamLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_downstream_cannot_hold_the_stream_forever(self):
        closed = asyncio.Event()
        async def content():
            try:
                yield b"first"
                await asyncio.Event().wait()
            finally:
                closed.set()
        iterator = content()
        disconnected = mock.Mock()
        response = _ManagedStreamingResponse(iterator, iterator.aclose, disconnected, timeout=0.01)
        async def blocked(*args):
            await asyncio.Event().wait()
        from starlette.requests import ClientDisconnect
        with self.assertRaises(ClientDisconnect):
            await asyncio.wait_for(response({}, blocked, blocked), 1)
        self.assertTrue(closed.is_set())
        disconnected.assert_called_once()

    async def test_deadline_cancels_upstream_and_closes_iterator(self):
        closed = asyncio.Event()
        async def endless():
            try:
                yield "first"
                await asyncio.Event().wait()
            finally:
                closed.set()
        service = CodeBuddyStreamService()
        iterator = service._deadline_stream(endless(), 0.01)
        self.assertEqual(await anext(iterator), "first")
        with self.assertRaises(UpstreamAPIError) as raised:
            await anext(iterator)
        self.assertEqual(raised.exception.status_code, 504)
        self.assertTrue(closed.is_set())

    async def test_error_read_is_bounded_and_does_not_echo_plain_text(self):
        service = CodeBuddyStreamService()
        response = httpx.Response(400, content=b"PRIVATE_UPSTREAM_BODY")
        with self.assertRaises(UpstreamAPIError) as raised:
            await service._raise_upstream_api_error(response)
        self.assertNotIn("PRIVATE_UPSTREAM_BODY", raised.exception.message)
        with mock.patch("config.get_security_limit", return_value=8):
            with self.assertRaises(UpstreamAPIError) as raised:
                await service._raise_upstream_api_error(response)
        self.assertEqual(raised.exception.status_code, 400)

    async def test_line_and_total_limits_include_comments_and_unterminated_lines(self):
        for chunks, line_limit, total_limit in ((("data: ", "123456"), 8, 100), ((": keepalive\n",)*4, 20, 25), (("data: {}\n",), 7, 100)):
            with self.assertRaises(SSEDataError):
                _ = [item async for item in iter_sse_events(async_chunks(*chunks), max_line_bytes=line_limit, max_total_bytes=total_limit)]
        self.assertEqual([item async for item in iter_sse_events(async_chunks("data: {}\n"), max_line_bytes=8, max_total_bytes=9)], [{}])


class PublicConfigurationTests(ConfigIsolationMixin, unittest.TestCase):
    def test_public_origin_forces_secure_cookie_even_on_internal_http(self):
        from src.auth_router import _is_secure_request
        from src.private_response import _is_secure_scope
        with mock.patch.dict(config._config_cache, {"CODEBUDDY_PUBLIC_ORIGIN":"https://public.example"}):
            self.assertTrue(_is_secure_request(make_request()))
            self.assertTrue(_is_secure_scope({"type":"http", "scheme":"http"}))

    def test_trusting_every_address_is_not_an_explicit_proxy_boundary(self):
        config._config_cache.update({"CODEBUDDY_PUBLIC_ORIGIN":"https://public.example", "CODEBUDDY_ALLOWED_HOSTS":"public.example", "CODEBUDDY_MAX_CONCURRENT_REQUESTS":64})
        for proxies in ("0.0.0.0/0", "::/0", "127.0.0.1/24"):
            with mock.patch.dict(config._config_cache, {"FORWARDED_ALLOW_IPS":proxies}):
                with self.assertRaises(ValueError):
                    config._validate_startup_config()

    def test_public_configuration_rejects_unsafe_combinations(self):
        config._config_cache.update({"CODEBUDDY_PUBLIC_ORIGIN":"https://public.example", "CODEBUDDY_ALLOWED_HOSTS":"public.example", "CODEBUDDY_MAX_CONCURRENT_REQUESTS":64})
        config._validate_startup_config()
        for key, value in (("CODEBUDDY_PUBLIC_ORIGIN","http://public.example"), ("CODEBUDDY_ALLOWED_HOSTS",""), ("CODEBUDDY_ALLOWED_HOSTS","*"), ("CODEBUDDY_ALLOWED_HOSTS","elsewhere.test"), ("CODEBUDDY_SSL_VERIFY",False), ("CODEBUDDY_MAX_CONCURRENT_REQUESTS",""), ("FORWARDED_ALLOW_IPS",""), ("FORWARDED_ALLOW_IPS","*"), ("FORWARDED_ALLOW_IPS","invalid")):
            with mock.patch.dict(config._config_cache, {key:value}):
                with self.assertRaises(ValueError, msg=key):
                    config._validate_startup_config()
