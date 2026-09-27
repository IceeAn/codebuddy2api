"""浏览器来源、并发和敏感信息边界的回归测试。"""
import tests

import unittest
from unittest import mock

import httpx
from fastapi import FastAPI

import config
from src.http_security import ExternalCORSMiddleware, SessionOriginMiddleware, normalize_origin
from tests.helpers import ConfigIsolationMixin, make_request


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
