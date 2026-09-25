"""真实 OpenAI SDK 通过 ASGI 调用网关，使用合成上游事件。"""

import tests  # 在生产模块导入前隔离测试数据目录。
import json
import unittest
from unittest import mock

import httpx2
import openai

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.session_store import session_store
from src.stream_service import CodeBuddyStreamService
from tests.helpers import FakeHttpClient, TempConfigMixin, configure_users_file
from web import app


def sse(delta, *, finish=None, usage=None):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta,
                                             "finish_reason": finish, "logprobs": None}],
                                 "usage": usage}) + "\n\n"


class OpenAISDKContractTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.key = api_key_store.create_key("admin", "sdk")["api_key"]
        self.prepared = []
        self.fixture = [
            sse({"reasoning_content": "思考", "content": "回答"}),
            sse({"tool_calls": [{"index": 0, "id": "call-test", "type": "function",
                                 "function": {"name": "lookup", "arguments": '{"q":'}}]}),
            sse({"tool_calls": [{"index": 0, "id": "", "function": {"name": "", "arguments": '"台北"}'}}],
                 "function_call": {"name": "", "arguments": ""}}, finish="tool_calls",
                usage={"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}),
            "data: [DONE]\n\n",
        ]

    async def _execute(self, prepared, _user, **_kwargs):
        self.prepared.append(prepared)
        service = CodeBuddyStreamService(
            http_client_factory=mock.AsyncMock(return_value=FakeHttpClient(self.fixture)),
            api_url_factory=lambda: "https://codebuddy.invalid/v2/chat/completions",
        )
        if prepared.client_wants_stream:
            return await service.handle_stream_response(prepared.payload, {}, response_model=prepared.response_model,
                                                        include_usage=prepared.client_include_usage)
        return await service.handle_non_stream_response(prepared.payload, {}, response_model=prepared.response_model)

    def _client(self, *, playground=False, key=None):
        headers = {}
        prefix = "/openai/v1"
        if playground:
            prefix = "/api/admin/playground/openai/v1"
            headers["Cookie"] = f"{SESSION_COOKIE_NAME}={session_store.create('admin')}"
        return openai.AsyncOpenAI(
            api_key=key or self.key, base_url="http://localhost" + prefix,
            http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
            default_headers=headers, max_retries=0,
        )

    @mock.patch("src.openai_router.create_usage_stats_context")
    async def test_sdk_image_tool_roundtrip_stream_usage_and_playground(self, _stats):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "查看图片"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            {"type": "image_url", "image_url": {"url": "https://example.invalid/test.png", "detail": "low"}},
        ]}]
        with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self._execute):
            async with self._client() as client:
                result = await client.chat.completions.create(model="kimi-k3-1", messages=messages, max_completion_tokens=32)
                self.assertEqual(result.choices[0].message.content, "回答")
                self.assertEqual(result.choices[0].message.reasoning_content, "思考")
                self.assertEqual(result.choices[0].message.tool_calls[0].function.arguments, '{"q":"台北"}')
                self.assertEqual(result.usage.total_tokens, 17)
                self.assertEqual(self.prepared[-1].payload["max_tokens"], 32)
                self.assertEqual(self.prepared[-1].payload["messages"][-1], messages[0])
                for include in (False, True):
                    stream = await client.chat.completions.create(
                        model="kimi-k3-1", messages=messages, stream=True, stream_options={"include_usage": include})
                    chunks = [chunk async for chunk in stream]
                    self.assertEqual("".join(c.choices[0].delta.content or "" for c in chunks if c.choices), "回答")
                    usage_chunks = [c for c in chunks if c.usage is not None]
                    self.assertEqual(len(usage_chunks), int(include))
                    if include:
                        self.assertEqual(usage_chunks[0].choices, [])
                        self.assertEqual(usage_chunks[0].usage.total_tokens, 17)
                history = messages + [result.choices[0].message.model_dump(exclude_none=True),
                                      {"role": "tool", "tool_call_id": "call-test", "content": "完成"}]
                await client.chat.completions.create(model="kimi-k3-1", messages=history)
                self.assertEqual(self.prepared[-1].payload["messages"][-2]["tool_calls"][0]["id"], "call-test")
            async with self._client(playground=True) as client:
                result = await client.chat.completions.create(model="kimi-k3-1", messages=messages)
                self.assertEqual(result.choices[0].message.content, "回答")

    async def test_sdk_typed_validation_and_authentication_errors(self):
        async with self._client() as client:
            with self.assertRaises(openai.BadRequestError) as caught:
                await client.chat.completions.create(model="test", messages=[{"role": "user", "content": "hi"}],
                                                     max_tokens=3, max_completion_tokens=4)
            self.assertEqual(caught.exception.body["param"], "max_completion_tokens")
        async with self._client(key="sk-invalid") as client:
            with self.assertRaises(openai.AuthenticationError):
                await client.chat.completions.create(model="test", messages=[{"role": "user", "content": "hi"}])

    @mock.patch("src.openai_router.create_usage_stats_context")
    async def test_sdk_special_assistant_message_roundtrip_without_content(self, _stats):
        for delta in ({"refusal": "拒绝"}, {"audio": {
            "id": "audio-test", "data": "YQ==", "expires_at": 123, "transcript": "音频文本"}}):
            with self.subTest(delta=delta):
                self.fixture = [sse(delta, finish="stop"), "data: [DONE]\n\n"]
                with mock.patch("src.openai_router.execute_codebuddy_chat", side_effect=self._execute):
                    async with self._client() as client:
                        result = await client.chat.completions.create(model="kimi", messages=[{"role": "user", "content": "测试"}])
                        history = result.choices[0].message.model_dump(exclude_none=True)
                        self.assertNotIn("content", history)
                        self.fixture = [sse({"content": "完成"}, finish="stop"), "data: [DONE]\n\n"]
                        await client.chat.completions.create(model="kimi", messages=[history, {"role": "user", "content": "继续"}])
                self.assertEqual(self.prepared[-1].payload["messages"][0], history)
