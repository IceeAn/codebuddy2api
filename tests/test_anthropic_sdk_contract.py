import json
import unittest
from unittest import mock

import anthropic
import httpx

from src.api_key_store import api_key_store
from src.stream_service import CodeBuddyStreamService
from tests.helpers import FakeHttpClient, TempConfigMixin, configure_users_file
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

    @mock.patch("src.anthropic_router.create_usage_stats_context", return_value=None)
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

    @mock.patch("src.anthropic_router.create_usage_stats_context", return_value=None)
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


if __name__ == "__main__":
    unittest.main()
