"""Anthropic 框架异常、请求体限制和会话错误契约。"""

import tests  # 在生产模块导入前隔离测试数据目录。

import unittest
from unittest import mock

import httpx
from fastapi import FastAPI, HTTPException, Request

from src.anthropic_router import anthropic_messages
from src.auth_error_codes import AuthBusinessError, AuthErrorCode
from src.auth_types import AuthenticatedUser, SESSION_COOKIE_NAME
from src.request_limits import RequestBodyLimitMiddleware
from src.session_store import session_store
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class AnthropicErrorContractTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)

    async def test_framework_errors_and_streamed_limit_use_anthropic_envelope(self):
        test_app = FastAPI(exception_handlers=app.exception_handlers)
        test_app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=2, login_max_body_bytes=2)

        @test_app.post("/anthropic/v1/test")
        async def endpoint(request: Request):
            await request.body()
            raise RuntimeError("secret")

        @test_app.post("/api/admin/playground/anthropic/v1/test")
        async def business_error():
            raise AuthBusinessError(403, AuthErrorCode.PASSWORD_CHANGE_REQUIRED)

        async def chunks():
            yield b"ab"
            yield b"c"

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app, raise_app_exceptions=False), base_url="http://localhost") as client:
            for content, status in ((b"", 500), (b"large", 413), (chunks(), 413)):
                response = await client.post("/anthropic/v1/test", content=content)
                self.assertEqual(response.status_code, status)
                body = response.json()
                self.assertEqual(body["type"], "error")
                self.assertEqual(body["request_id"], response.headers["request-id"])
                self.assertNotIn("secret", response.text)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")
            response = await client.post("/api/admin/playground/anthropic/v1/test")
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["type"], "permission_error")

    async def test_json_body_limit_is_not_reclassified_as_invalid_json(self):
        request = mock.Mock()
        request.state.anthropic_request_id = "req_test"
        request.json = mock.AsyncMock(side_effect=HTTPException(413, "请求体超过允许上限"))
        with self.assertRaises(HTTPException) as raised:
            await anthropic_messages(request, AuthenticatedUser(username="admin", source="api_key"), stats_context=mock.Mock())
        self.assertEqual(raised.exception.status_code, 413)
        stats = mock.Mock()
        with self.assertRaises(HTTPException) as raised:
            await anthropic_messages(request, AuthenticatedUser(username="admin", source="api_key"), stats_context=stats)
        self.assertEqual(raised.exception.status_code, 413)
        stats.mark_failure.assert_called_once_with("validation_error", 413)

    async def test_revoked_playground_session_expires_cookie(self):
        session_id = session_store.create("admin")
        session_store.revoke_user("admin", AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE.value)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/admin/playground/anthropic/v1/messages", json={},
                                         headers={"Origin": "http://localhost", "anthropic-version": "2023-06-01", "Cookie": f"{SESSION_COOKIE_NAME}={session_id}"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["type"], "authentication_error")
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
