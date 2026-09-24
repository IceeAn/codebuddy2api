
import tests  # 在生产模块导入前隔离测试数据目录。
import json
import unittest
from unittest import mock

import anthropic
import httpx

from src.api_key_store import api_key_store
from src.stream_service import CodeBuddyStreamService
from tests.helpers import FakeHttpClient, TempConfigMixin, configure_users_file
from tests.test_anthropic_multimodal import IMAGE, URL_IMAGE, TEXT_DOCUMENT, MAPPED_IMAGE, MAPPED_URL
from web import app


def sse(data):
    value = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    return f"data: {value}\n\n"


class AnthropicSDKContractTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.api_key = api_key_store.create_key("admin", "sdk")['api_key']
        self.fixture = [
            sse({"choices": [{"delta": {"reasoning_content": "consider"}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"content": "answer"}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "id": "tool_sdk",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city":'},
            }]}, "finish_reason": None}]}),
            sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0,
                "function": {"arguments": '"Taipei"}'},
            }]}, "finish_reason": "tool_calls"}]}),
            sse({"choices": [], "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 5,
                "total_tokens": 17,
            }}),
            sse("[DONE]"),
        ]

    async def _execute(self, prepared, _user, *, response_adapter, **_kwargs):
        self.last_payload = prepared.payload
        fake_client = FakeHttpClient(self.fixture)
        service = CodeBuddyStreamService(
            http_client_factory=mock.AsyncMock(return_value=fake_client),
            api_url_factory=lambda: "https://codebuddy.invalid/v2/chat/completions",
        )
        if prepared.client_wants_stream:
            return await service.handle_stream_response(
                prepared.payload,
                {},
                response_model=prepared.response_model,
                response_adapter=response_adapter,
            )
        return await service.handle_non_stream_response(
            prepared.payload,
            {},
            response_model=prepared.response_model,
            response_adapter=response_adapter,
        )

    def _client(self, api_key=None, *, default_headers=None):
        http_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost/anthropic",
        )
        client = anthropic.AsyncAnthropic(
            api_key=api_key or self.api_key,
            base_url="http://localhost/anthropic",
            http_client=http_client,
            max_retries=0,
            default_headers=default_headers,
        )
        return client, http_client

    @mock.patch("src.anthropic_router.create_usage_stats_context")
    async def test_sdk_multimodal_tool_round_trip_and_playground(self, _stats):
        from src.auth_types import SESSION_COOKIE_NAME
        from src.session_store import session_store
        client, _ = self._client()
        messages = [{"role": "user", "content": [IMAGE, {"type": "text", "text": "识别"}, URL_IMAGE]}]
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        options = {"model": "kimi-k3-1", "max_tokens": 256,
                   "tools": [{"name": "weather", "input_schema": {"type": "object"}, "strict": True}],
                   "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": "low"}, "top_k": 12}
        try:
            with mock.patch("src.anthropic_router.execute_codebuddy_chat", side_effect=self._execute):
                complete = await client.messages.create(messages=messages, **options)
                self.assertEqual(self.last_payload["messages"][-1]["content"], [MAPPED_IMAGE, {"type": "text", "text": "识别"}, MAPPED_URL])
                self.assertEqual(self.last_payload["response_format"]["json_schema"]["schema"], schema)
                self.assertTrue(self.last_payload["tools"][0]["function"]["strict"])
                self.assertEqual((self.last_payload["top_k"], self.last_payload["reasoning_effort"]), (12, "low"))
                history = [*messages, {"role": "assistant", "content": [block.model_dump() for block in complete.content]},
                           {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool_sdk",
                                                           "content": [IMAGE, TEXT_DOCUMENT], "is_error": True}]}]
                async with client.messages.stream(messages=history, **options) as stream:
                    streamed = await stream.get_final_message()
                self.assertEqual(streamed.stop_reason, "tool_use")
                self.assertEqual(self.last_payload["messages"][-1]["role"], "tool")
                self.assertEqual(self.last_payload["messages"][-1]["tool_call_id"], "tool_sdk")
                self.assertEqual(self.last_payload["messages"][-1]["content"][1], MAPPED_IMAGE)
                self.assertEqual(self.last_payload["messages"][-2]["reasoning_content"], "consider")
                cookie = session_store.create("admin")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http_client:
                    response = await http_client.post("/api/admin/playground/anthropic/v1/messages",
                                                      headers={"anthropic-version": "2023-06-01", "Cookie": f"{SESSION_COOKIE_NAME}={cookie}"},
                                                      json={**options, "messages": history})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["content"][-1]["id"], "tool_sdk")
        finally:
            await client.close()

    @mock.patch("src.anthropic_router.create_usage_stats_context")
    async def test_sdk_refusal_and_unconstrained_output_are_not_lost_or_repaired(self, _stats):
        client, _ = self._client()
        try:
            with mock.patch("src.anthropic_router.execute_codebuddy_chat", side_effect=self._execute):
                for field, answer, expected_reason in (("refusal", "无法回答", "refusal"), ("content", "非 JSON", "end_turn")):
                    self.fixture = [sse({"choices": [{"delta": {field: answer}, "finish_reason": "stop"}]}),
                                    sse({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 2}}), sse("[DONE]")]
                    options = {"model": "kimi-k3-1", "max_tokens": 128, "messages": [{"role": "user", "content": "hi"}],
                               "output_config": {"format": {"type": "json_schema", "schema": {"type": "object"}}}}
                    complete = await client.messages.create(**options)
                    async with client.messages.stream(**options) as stream:
                        streamed = await stream.get_final_message()
                    for result in (complete, streamed):
                        self.assertEqual(result.content[0].text, answer)
                        self.assertEqual(result.stop_reason, expected_reason)
        finally:
            await client.close()

    @mock.patch("src.anthropic_router.create_usage_stats_context")
    @mock.patch(
        "src.anthropic_router._available_models",
        new_callable=mock.AsyncMock,
        return_value=["glm"],
    )
    async def test_sdk_parses_non_stream_and_stream_into_equivalent_messages(
            self,
            _models,
            _stats,
    ):
        client, http_client = self._client()
        try:
            with mock.patch("src.anthropic_router.execute_codebuddy_chat", side_effect=self._execute):
                complete = await client.messages.create(
                    model="anthropic/codebuddy/glm",
                    max_tokens=128,
                    messages=[{"role": "user", "content": "hello"}],
                    tools=[{
                        "name": "weather",
                        "description": "Weather",
                        "input_schema": {"type": "object"},
                    }],
                )
                async with client.messages.stream(
                    model="anthropic/codebuddy/glm",
                    max_tokens=128,
                    messages=[{"role": "user", "content": "hello"}],
                    tools=[{
                        "name": "weather",
                        "description": "Weather",
                        "input_schema": {"type": "object"},
                    }],
                ) as stream:
                    event_types = []
                    async for sdk_event in stream:
                        event_types.append(sdk_event.type)
                    streamed = await stream.get_final_message()

            self.assertEqual([block.type for block in complete.content], ["thinking", "text", "tool_use"])
            self.assertEqual(complete.content[0].thinking, "consider")
            self.assertTrue(complete.content[0].signature.startswith("cb2a_"))
            self.assertEqual(complete.content[1].text, "answer")
            self.assertEqual(complete.content[2].input, {"city": "Taipei"})
            self.assertEqual(complete.stop_reason, "tool_use")
            self.assertEqual(complete.usage.input_tokens, 12)
            self.assertEqual(complete.usage.output_tokens, 5)
            self.assertEqual(
                [block.model_dump(exclude={"parsed_output"}) for block in streamed.content],
                [block.model_dump() for block in complete.content],
            )
            self.assertEqual(streamed.stop_reason, complete.stop_reason)
            self.assertEqual(streamed.usage, complete.usage)
            self.assertIn("message_start", event_types)
            self.assertEqual(event_types[-1], "message_stop")
        finally:
            await client.close()
            if not http_client.is_closed:
                await http_client.aclose()

    @mock.patch("src.anthropic_router.create_usage_stats_context")
    async def test_sdk_parses_anthropic_error_and_request_id(self, _stats):
        client, http_client = self._client(api_key="sk-invalid")
        try:
            with self.assertRaises(anthropic.AuthenticationError) as raised:
                await client.messages.create(
                    model="glm",
                    max_tokens=10,
                    messages=[{"role": "user", "content": "hello"}],
                )
            self.assertTrue(raised.exception.request_id.startswith("req_"))
            self.assertEqual(raised.exception.body["type"], "error")
        finally:
            await client.close()
            if not http_client.is_closed:
                await http_client.aclose()

    @mock.patch(
        "src.anthropic_router._available_models",
        new_callable=mock.AsyncMock,
        return_value=["glm"],
    )
    async def test_sdk_models_list_has_all_required_model_info_fields(self, _models):
        client, http_client = self._client()
        try:
            page = await client.models.list()

            self.assertEqual(len(page.data), 1)
            model = page.data[0]
            self.assertEqual(model.id, "anthropic/codebuddy/glm")
            self.assertEqual(model.type, "model")
            self.assertEqual(model.created_at.isoformat(), "1970-01-01T00:00:00+00:00")
            self.assertEqual(model.display_name, "glm")
        finally:
            await client.close()
            if not http_client.is_closed:
                await http_client.aclose()


if __name__ == "__main__":
    unittest.main()
