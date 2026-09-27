"""OpenAI 路由及中间件错误保持统一且不泄露请求值。"""

import tests  # 在生产模块导入前隔离测试数据目录。
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from src.api_key_store import api_key_store
from src.request_limits import RequestBodyLimitMiddleware
from src.stream_service import CodeBuddyStreamService, UpstreamAPIError
from tests.helpers import TempConfigMixin, configure_users_file
from web import app, request_validation_error_handler, upstream_api_error_handler


class OpenAIErrorContractTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.api_key = api_key_store.create_key("admin", "error-test")["api_key"]

    async def test_basic_validation_errors_identify_fields_on_both_routes(self):
        from src.auth_types import SESSION_COOKIE_NAME
        from src.session_store import session_store
        session_id = session_store.create("admin")
        cases = [({}, "messages"), ({"messages": []}, "messages"),
                 ({"messages": "secret"}, "messages"), ({"messages": ["secret"]}, "messages[0]"),
                 ({"messages": [{"content": "secret"}]}, "messages[0].role"),
                 ({"messages": [{"role": "user"}]}, "messages[0].content"),
                 ({"messages": [{"role": "assistant", "tool_calls": []}]}, "messages[0].content"),
                 ([], None)]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            for path, headers in (
                ("/openai/v1/chat/completions", {"Authorization": "Bearer " + self.api_key}),
                ("/api/admin/playground/openai/v1/chat/completions", {"Origin": "http://localhost", "Cookie": f"{SESSION_COOKIE_NAME}={session_id}"}),
            ):
                for body, param in cases:
                    with self.subTest(path=path, body=body):
                        response = await client.post(path, headers=headers, json=body)
                        self.assertEqual(response.status_code, 400)
                        error = response.json()["error"]
                        self.assertEqual(error["param"], param)
                        self.assertEqual(error["type"], "invalid_request_error")
                        self.assertIsNone(error["code"])
                        self.assertNotIn("secret", response.text)

    async def test_route_validation_authentication_and_missing_paths(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            for path, auth, body, status in [
                ("/openai/v1/chat/completions", {}, {}, 401),
                ("/api/admin/playground/openai/v1/chat/completions", {}, {}, 401),
                ("/openai/v1/chat/completions", {"Authorization": "Bearer " + self.api_key}, {}, 400),
                ("/openai/v1/chat/completions", {"Authorization": "Bearer " + self.api_key},
                 {"messages": [{"role": "user", "content": "secret"}], "max_tokens": 0}, 400),
                ("/openai/v1/missing", {}, {}, 404),
            ]:
                response = await client.post(path, headers={"Origin": "http://localhost", **auth}, json=body)
                with self.subTest(path=path, status=status):
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(set(response.json()["error"]), {"message", "type", "param", "code"})
                    self.assertNotIn("secret", response.text)
                    self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_body_limit_and_uncaught_server_error(self):
        test_app = FastAPI(exception_handlers=app.exception_handlers)
        test_app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=2, login_max_body_bytes=2)

        @test_app.post("/openai/v1/test")
        async def endpoint(request: Request):
            await request.body()
            raise RuntimeError("secret error")

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app, raise_app_exceptions=False),
                                     base_url="http://localhost") as client:
            for content, status in [(b"large", 413), (b"", 500)]:
                response = await client.post("/openai/v1/test", content=content)
                self.assertEqual(response.status_code, status)
                self.assertEqual(set(response.json()["error"]), {"message", "type", "param", "code"})
                self.assertNotIn("secret", response.text)

    async def test_upstream_msg_is_extracted_without_raw_error_body(self):
        message, _, code = CodeBuddyStreamService._parse_upstream_error_body(
            '{"code":11101,"msg":"unsupported content type: file","requestId":"secret"}')
        self.assertEqual(message, "unsupported content type: file")
        self.assertEqual(code, 11101)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=UpstreamAPIError(
                    429, "rate limited", "rate_limit_error", code="limited", headers={"Retry-After": "3"})):
                response = await client.post("/openai/v1/chat/completions",
                                             headers={"Authorization": "Bearer " + self.api_key},
                                             json={"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(response.json()["error"]["code"], "limited")
        self.assertIsNone(response.json()["error"]["param"])
        self.assertEqual(response.headers["Retry-After"], "3")

    async def test_dependency_error_does_not_echo_raw_input(self):
        result = await request_validation_error_handler(
            Request({"type": "http", "path": "/openai/v1/chat/completions", "headers": []}),
            RequestValidationError([{"input": "secret", "loc": ["header", "test"]}]),
        )
        self.assertEqual(result.status_code, 400)
        self.assertNotIn(b"secret", result.body)

    async def test_playground_password_revocation_uses_protocol_error_and_expires_cookie(self):
        from src.auth_error_codes import AuthErrorCode
        from src.auth_types import SESSION_COOKIE_NAME
        from src.session_store import session_store
        session_id = session_store.create("admin")
        session_store.revoke_user("admin", AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE.value)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/admin/playground/openai/v1/chat/completions",
                                         headers={"Origin": "http://localhost", "Cookie": f"{SESSION_COOKIE_NAME}={session_id}"}, json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "password_changed_elsewhere")
        self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])

    async def test_admin_upstream_error_keeps_existing_envelope(self):
        response = await upstream_api_error_handler(
            Request({"type": "http", "path": "/api/admin/test", "headers": []}),
            UpstreamAPIError(502, "upstream failed", "upstream_error"),
        )
        self.assertEqual(response.status_code, 502)
        self.assertIn(b'"error"', response.body)

    async def test_streamed_body_limit_keeps_413_even_when_json_parser_catches_it(self):
        from src.auth_types import AuthenticatedUser
        from src.openai_router import chat_completions
        request = mock.Mock()
        request.json = mock.AsyncMock(side_effect=HTTPException(413, "请求体超过允许上限"))
        with self.assertRaises(HTTPException) as caught:
            await chat_completions(request, AuthenticatedUser(username="admin", source="api_key"), stats_context=mock.Mock())
        self.assertEqual(caught.exception.status_code, 413)

        test_app = FastAPI(exception_handlers=app.exception_handlers)
        test_app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=2, login_max_body_bytes=2)

        @test_app.post("/openai/v1/test")
        async def endpoint(request: Request):
            await request.body()

        async def chunks():
            yield b"ab"
            yield b"c"

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url="http://localhost") as client:
            response = await client.post("/openai/v1/test", content=chunks())
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()["error"]["type"], "invalid_request_error")
