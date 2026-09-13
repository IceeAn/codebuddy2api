"""Responses 路由、鉴权和官方 SDK 契约。"""
import json
import unittest
from unittest import mock

import httpx
import openai
import config

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.session_store import session_store
from src.stream_service import CodeBuddyStreamService
from tests.helpers import FakeHttpClient, TempConfigMixin, configure_users_file
from web import app


class ResponsesRoutesTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.key = api_key_store.create_key("admin", "responses")["api_key"]
        self.session = session_store.create("admin")
        self.fixture = [
            {"choices": [{"delta": {"reasoning_content": "考虑"}}]},
            {"choices": [{"delta": {"content": "回答"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
        ]
        self.payloads = []

    async def execute(self, prepared, _user, *, response_adapter, **_kwargs):
        self.payloads.append(prepared.payload)
        wire = [f"data: {json.dumps(item)}\n\n" for item in self.fixture] + ["data: [DONE]\n\n"]
        service = CodeBuddyStreamService(http_client_factory=mock.AsyncMock(return_value=FakeHttpClient(wire)),
                                        api_url_factory=lambda: "https://codebuddy.invalid/v2/chat/completions")
        method = service.handle_stream_response if prepared.client_wants_stream else service.handle_non_stream_response
        return await method(prepared.payload, {}, response_adapter=response_adapter)

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")

    async def test_truncated_tool_arguments_return_incomplete_for_both_transports(self):
        async with self.client() as client:
            for finish in ("length", "content_filter"):
                for stream in (False, True):
                    for kind in ("function", "custom"):
                        with self.subTest(finish=finish, stream=stream, kind=kind):
                            self.fixture = [{"choices": [{"delta": {"tool_calls": [
                                {"index": 0, "id": "original", "function": {"name": "tool", "arguments": '{"input":"partial'}},
                            ]}, "finish_reason": finish}]}]
                            with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self.execute):
                                result = await client.post("/openai/v1/responses", headers={"Authorization": "Bearer " + self.key},
                                                           json={"model": "kimi", "input": "测试截断", "stream": stream,
                                                                 "tools": [{"type": kind, "name": "tool"}]})
                            self.assertEqual(result.status_code, 200, result.text)
                            if stream:
                                events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: ")]
                                self.assertEqual(events[-1]["type"], "response.incomplete")
                                self.assertFalse(any(item["type"] == "response.failed" for item in events))
                                final = events[-1]["response"]
                            else:
                                final = result.json()
                            self.assertEqual(final["status"], "incomplete")
                            self.assertEqual(final["incomplete_details"]["reason"], "max_output_tokens" if finish == "length" else finish)
                            self.assertEqual(final["output"], [])

    async def test_authentication_isolation_and_error_envelope(self):
        async with self.client() as client:
            for path, headers, expected in (
                ("/openai/v1/responses", {"Authorization": "Bearer " + self.key}, 200),
                ("/openai/v1/responses", {"Cookie": f"{SESSION_COOKIE_NAME}={self.session}"}, 401),
                ("/api/admin/playground/openai/v1/responses", {"Cookie": f"{SESSION_COOKIE_NAME}={self.session}"}, 200),
                ("/api/admin/playground/openai/v1/responses", {"Authorization": "Bearer " + self.key}, 401),
            ):
                client.cookies.clear()
                with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self.execute):
                    result = await client.post(path, headers=headers, json={"model": "kimi", "input": "hello"})
                self.assertEqual(result.status_code, expected, result.text)
                self.assertEqual(result.headers["cache-control"], "private, no-store")
            rejected = await client.post("/openai/v1/responses", headers={"Authorization": "Bearer " + self.key}, json={"model": "kimi", "input": "x", "store": True})
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(rejected.json()["error"]["param"], "store")

    async def test_official_sdk_stream_aggregation_and_images(self):
        async with self.client() as http_client:
            client = openai.AsyncOpenAI(api_key=self.key, base_url="http://localhost/openai/v1", http_client=http_client, max_retries=0)
            with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self.execute):
                result = await client.responses.create(model="kimi", input="hello")
                async with client.responses.stream(model="kimi", input=[{"role": "user", "content": [
                    {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
                    {"type": "input_text", "text": "看图"}]}]) as stream:
                    types = [item.type async for item in stream]
                    final = await stream.get_final_response()
            self.assertEqual(result.output_text, "回答")
            self.assertEqual(final.output_text, "回答")
            self.assertEqual(final.usage.input_tokens, 10)
            self.assertIn("response.reasoning_summary_text.delta", types)
            self.assertEqual(types[-1], "response.completed")
            self.assertEqual(self.payloads[-1]["messages"][1]["content"][0]["type"], "image_url")

    async def test_sdk_custom_call_roundtrip(self):
        self.fixture = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "original_id", "function": {"name": "patch", "arguments": '{"input":"原始\\n补丁"}'}}]}, "finish_reason": "tool_calls"}]}]
        async with self.client() as http_client:
            client = openai.AsyncOpenAI(api_key=self.key, base_url="http://localhost/openai/v1", http_client=http_client, max_retries=0)
            with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self.execute):
                async with client.responses.stream(model="kimi", input="patch", tools=[{"type": "custom", "name": "patch"}]) as stream:
                    async for _ in stream:
                        pass
                    final = await stream.get_final_response()
                self.assertEqual(final.output[0].input, "原始\n补丁")
                self.assertEqual(final.output[0].call_id, "original_id")
                self.fixture = [{"choices": [{"delta": {"content": "完成"}, "finish_reason": "stop"}]}]
                await client.responses.create(model="kimi", input=[final.output[0].model_dump(exclude_none=True), {"type": "custom_tool_call_output", "call_id": "original_id", "output": "ok"}])
            self.assertEqual(self.payloads[-1]["messages"][1]["tool_call_id"], "original_id")

    async def test_parallel_choice_is_preserved_in_response(self):
        async with self.client() as client:
            with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self.execute):
                result = await client.post("/openai/v1/responses", headers={"Authorization": "Bearer " + self.key},
                                           json={"model": "kimi", "input": "hello", "parallel_tool_calls": False})
            self.assertIs(result.json()["parallel_tool_calls"], False)

    async def test_auto_review_alias_uses_authenticated_users_model(self):
        config.update_settings({"CODEBUDDY_CODEX_AUTO_REVIEW_MODEL": "kimi-k3-1"}, username="admin")
        async with self.client() as client:
            with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self.execute):
                result = await client.post("/openai/v1/responses", headers={"Authorization": "Bearer " + self.key},
                                           json={"model": "codex-auto-review", "input": "审批测试"})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["model"], "codex-auto-review")
            self.assertEqual(self.payloads[-1]["model"], "kimi-k3-1")

    def test_schema_exposes_only_external_responses(self):
        app.openapi_schema = None
        paths = app.openapi()["paths"]
        self.assertIn("/openai/v1/responses", paths)
        self.assertNotIn("/api/admin/playground/openai/v1/responses", paths)
