import json
import unittest
from unittest import mock

from fastapi import HTTPException

from src.auth_types import AuthenticatedUser
from src.codebuddy_router import list_v1_models
from src.stream_service import CodeBuddyStreamService, StreamResponseAggregator

from tests.helpers import FakeHttpClient


class CodeBuddyRouterModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_list_returns_minimal_openai_model_objects(self):
        with mock.patch("src.codebuddy_router.get_available_models_list", lambda: ["deepseek-v4-pro", "glm-5.1"]):
            response = await list_v1_models(AuthenticatedUser(username="admin", source="users_file"))

        models = {item["id"]: item for item in response["data"]}

        self.assertEqual(models["deepseek-v4-pro"]["object"], "model")
        self.assertEqual(models["deepseek-v4-pro"]["owned_by"], "codebuddy")
        self.assertIsInstance(models["deepseek-v4-pro"]["created"], int)
        self.assertNotIn("reasoning", models["deepseek-v4-pro"])
        self.assertNotIn("limit", models["glm-5.1"])


class StreamResponseAggregatorTests(unittest.TestCase):
    def test_finalize_defaults_empty_stream_to_openai_response_shape(self):
        response = StreamResponseAggregator().finalize()

        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["model"], "unknown")
        self.assertEqual(response["choices"][0]["message"], {"role": "assistant", "content": ""})
        self.assertEqual(response["choices"][0]["finish_reason"], "stop")
        self.assertEqual(response["choices"][0]["logprobs"], None)

    def test_aggregator_preserves_usage_and_system_fingerprint(self):
        aggregator = StreamResponseAggregator()

        aggregator.process_chunk({
            "id": "chatcmpl-1",
            "model": "glm-5.2",
            "system_fingerprint": "fp",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}],
        })
        response = aggregator.finalize()

        self.assertEqual(response["id"], "chatcmpl-1")
        self.assertEqual(response["model"], "glm-5.2")
        self.assertEqual(response["usage"], {"prompt_tokens": 1, "completion_tokens": 2})
        self.assertEqual(response["system_fingerprint"], "fp")

    def test_aggregator_repairs_joined_tool_arguments(self):
        aggregator = StreamResponseAggregator()

        aggregator.process_chunk({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"a":1}'},
                            },
                            {
                                "function": {"arguments": '{"b":2}'},
                            },
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        })
        response = aggregator.finalize()

        tool_call = response["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(tool_call["function"]["arguments"], '{"a": 1}')
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")


class StreamingFormatTests(unittest.IsolatedAsyncioTestCase):
    async def _render_stream_body(self, chunks, model="glm-5.1", status_code=200, text=""):
        async def fake_get_http_client():
            return FakeHttpClient(chunks, status_code=status_code, text=text)

        response = await CodeBuddyStreamService(http_client_factory=fake_get_http_client).handle_stream_response(
            {"model": model},
            {},
        )
        body_parts = []
        async for part in response.body_iterator:
            body_parts.append(part.decode("utf-8") if isinstance(part, bytes) else part)

        return "".join(body_parts)

    async def _render_non_stream_response(self, chunks, model="glm-5.1", status_code=200, text=""):
        async def fake_get_http_client():
            return FakeHttpClient(chunks, status_code=status_code, text=text)

        return await CodeBuddyStreamService(http_client_factory=fake_get_http_client).handle_non_stream_response(
            {"model": model},
            {},
        )

    def _stream_payloads(self, body):
        events = [event for event in body.split("\n\n") if event]
        return [
            json.loads(event[6:])
            for event in events
            if event.startswith("data: ") and event != "data: [DONE]"
        ]

    async def test_stream_response_uses_openai_sse_event_boundaries(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        events = [event for event in body.split("\n\n") if event]

        self.assertEqual(len(events), 3)
        self.assertEqual(events[2], "data: [DONE]")
        self.assertTrue(events[0].startswith("data: "))

        payload = json.loads(events[0][6:])
        self.assertEqual(payload["object"], "chat.completion.chunk")
        self.assertEqual(payload["model"], "glm-5.1")
        self.assertIn("id", payload)
        self.assertIn("created", payload)
        self.assertEqual(payload["choices"][0]["delta"], {"role": "assistant"})

        content_payload = json.loads(events[1][6:])
        self.assertEqual(content_payload["id"], payload["id"])
        self.assertEqual(content_payload["created"], payload["created"])
        self.assertEqual(content_payload["choices"][0]["delta"]["content"], "hi")

    async def test_stream_response_normalizes_reasoning_chunks_for_opencode(self):
        chunks = [
            'data: {"id":"upstream-1","created":1,"model":"wrong","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"我","content":"","function_call":null,"refusal":null,"tool_calls":[],"extra_fields":{}},"finish_reason":null}]}\n'
            'data: {"id":"upstream-2","created":2,"model":"wrong","choices":[{"index":0,"delta":{"role":"","reasoning_content":"需要","content":null},"finish_reason":null}]}\n'
            'data: {"id":"upstream-3","created":3,"model":"wrong","choices":[{"index":0,"delta":{"role":"","reasoning_content":"先","content":""},"finish_reason":null}]}\n'
            'data: {"id":"upstream-4","created":4,"model":"wrong","choices":[{"index":0,"delta":{"role":"","content":"结论"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)

        self.assertEqual([payload["choices"][0]["delta"] for payload in payloads], [
            {"role": "assistant"},
            {"reasoning_content": "我"},
            {"reasoning_content": "需要"},
            {"reasoning_content": "先"},
            {"content": "结论"},
        ])
        self.assertEqual(len({payload["id"] for payload in payloads}), 1)
        self.assertEqual(len({payload["created"] for payload in payloads}), 1)
        self.assertEqual({payload["model"] for payload in payloads}, {"glm-5.1"})

    async def test_stream_response_removes_empty_function_call_object(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"content":"结论"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"role":"","function_call":{"name":"","arguments":""},"refusal":"","extra_fields":null},"finish_reason":"stop"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)

        deltas = [payload["choices"][0]["delta"] for payload in payloads]
        self.assertEqual(deltas, [{"role": "assistant"}, {"content": "结论"}, {}])
        self.assertEqual(payloads[2]["choices"][0]["finish_reason"], "stop")

    async def test_stream_response_preserves_content_after_tool_calls(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"先想"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"tooluse_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"content":"继续思考"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)
        deltas = [payload["choices"][0]["delta"] for payload in payloads]

        self.assertEqual(deltas[0], {"role": "assistant"})
        self.assertEqual(deltas[1], {"reasoning_content": "先想"})
        self.assertEqual(deltas[2]["tool_calls"][0]["id"], "tooluse_1")
        self.assertEqual(deltas[3], {"content": "继续思考"})
        self.assertEqual(deltas[4], {})
        self.assertEqual(payloads[4]["choices"][0]["finish_reason"], "tool_calls")

    async def test_stream_response_keeps_content_and_reasoning_after_tool_calls_separate(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"tooluse_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"content":"工具后文本"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"继续推理"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)
        deltas = [payload["choices"][0]["delta"] for payload in payloads]

        self.assertEqual(deltas[0], {"role": "assistant"})
        self.assertEqual(deltas[2], {"content": "工具后文本"})
        self.assertEqual(deltas[3], {"reasoning_content": "继续推理"})

    async def test_stream_response_keeps_reasoning_after_tool_calls(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"tooluse_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"工具后继续想"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)
        deltas = [payload["choices"][0]["delta"] for payload in payloads]

        self.assertEqual(deltas[0], {"role": "assistant"})
        self.assertEqual(deltas[2], {"reasoning_content": "工具后继续想"})
        self.assertFalse(any(delta.get("content") == "工具后继续想" for delta in deltas))

    async def test_stream_response_formats_upstream_api_error_as_sse_error(self):
        body = await self._render_stream_body([], status_code=429, text="too many")
        payloads = self._stream_payloads(body)

        self.assertEqual(payloads[0]["error"]["type"], "api_error")
        self.assertIn("429", payloads[0]["error"]["message"])

    async def test_non_stream_response_preserves_reasoning_and_content_after_tool_calls(self):
        chunks = [
            'data: {"id":"chatcmpl-1","model":"glm-5.1","choices":[{"index":0,"delta":{"reasoning_content":"先想"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"content":"继续思考"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        response = await self._render_non_stream_response(chunks)
        message = response["choices"][0]["message"]

        self.assertEqual(message["content"], "继续思考")
        self.assertEqual(message["reasoning_content"], "先想")
        self.assertEqual(message["tool_calls"][0]["id"], "call_1")
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")

    async def test_non_stream_response_maps_upstream_api_error(self):
        with self.assertRaises(HTTPException) as context:
            await self._render_non_stream_response([], status_code=429, text="rate limited")

        self.assertEqual(context.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
