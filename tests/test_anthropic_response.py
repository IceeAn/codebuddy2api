import json
import unittest
from types import SimpleNamespace
from unittest import mock

import src.anthropic_response as response_module
from src.anthropic_compat import anthropic_thinking_signature
from src.anthropic_response import (
    AnthropicNonStreamAggregator,
    AnthropicResponseContext,
    AnthropicStreamEncoder,
    UpstreamProtocolViolation,
    AnthropicDownstreamAdapter,
    format_anthropic_stream_error,
    map_anthropic_error,
    map_usage,
)
from src.codebuddy_events import CodeBuddyResponseEvent
from src.stream_service import CodeBuddyStreamService, UpstreamAPIError
from tests.helpers import FakeHttpClient


def event(delta=None, *, finish=None, usage=None):
    value = {}
    if delta is not None or finish is not None:
        value["choices"] = [{"delta": delta or {}, "finish_reason": finish}]
    if usage is not None:
        value["usage"] = usage
    return CodeBuddyResponseEvent.parse(value)


def decode_sse(chunks):
    decoded = []
    for chunk in chunks:
        lines = chunk.strip().splitlines()
        decoded.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return decoded


class AnthropicNonStreamResponseTests(unittest.TestCase):
    def setUp(self):
        self.context = AnthropicResponseContext("msg_test", "req_test", "anthropic/codebuddy/glm")

    def test_reasoning_text_tools_usage_and_model(self):
        aggregator = AnthropicNonStreamAggregator(self.context)
        for item in [
            event({"reasoning_content": "think"}),
            event({"reasoning_content": " more", "content": "answer"}),
            event({"content": " one"}),
            event({"reasoning_content": "again"}),
            event({"tool_calls": [
                {"index": 1, "id": "two", "type": "function", "function": {"name": "b", "arguments": "{\"b\":"}},
                {"index": 0, "id": "one", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            ]}),
            event({"tool_calls": [{"index": 1, "function": {"arguments": "2}"}}]}, finish="tool_calls"),
            event(usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}),
        ]:
            aggregator.process_event(item)

        response = aggregator.finalize()
        self.assertEqual(response, {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "anthropic/codebuddy/glm",
            "content": [
                {"type": "thinking", "thinking": "think more", "signature": anthropic_thinking_signature("think more")},
                {"type": "text", "text": "answer one"},
                {"type": "thinking", "thinking": "again", "signature": anthropic_thinking_signature("again")},
                {"type": "tool_use", "id": "one", "name": "a", "input": {}},
                {"type": "tool_use", "id": "two", "name": "b", "input": {"b": 2}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 4},
        })

    def test_finish_reason_mapping(self):
        expected = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
            "function_call": "tool_use",
            "content_filter": "refusal",
        }
        for finish, stop_reason in expected.items():
            with self.subTest(finish=finish):
                aggregator = AnthropicNonStreamAggregator(self.context)
                aggregator.process_event(event({"content": "x"}, finish=finish))
                aggregator.process_event(event(usage={"prompt_tokens": 1, "completion_tokens": 2}))
                self.assertEqual(aggregator.finalize()["stop_reason"], stop_reason)

    def test_content_filter_without_usage_returns_compatible_zero_usage(self):
        aggregator = AnthropicNonStreamAggregator(self.context)
        aggregator.process_event(event({"content": "refused"}, finish="content_filter"))

        response = aggregator.finalize()

        self.assertEqual(response["stop_reason"], "refusal")
        self.assertEqual(response["usage"], {"input_tokens": 0, "output_tokens": 0})

    def test_protocol_failures_are_strict(self):
        cases = [
            [event({"content": 1}, finish="stop"), event(usage={"prompt_tokens": 1, "completion_tokens": 1})],
            [event({"tool_calls": {}}, finish="tool_calls"), event(usage={"prompt_tokens": 1, "completion_tokens": 1})],
            [event({"tool_calls": [{"index": 0, "id": "x", "function": {"name": "a", "arguments": "[]"}}]}, finish="tool_calls"), event(usage={"prompt_tokens": 1, "completion_tokens": 1})],
            [event({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}, finish="tool_calls"), event(usage={"prompt_tokens": 1, "completion_tokens": 1})],
            [event({"content": "x"}, finish="mystery"), event(usage={"prompt_tokens": 1, "completion_tokens": 1})],
            [event({"content": "x"}, finish="stop")],
            [event({"content": "x"}), event(usage={"prompt_tokens": 1, "completion_tokens": 1})],
            [event({"content": "x"}, finish="stop"), event(usage={"prompt_tokens": True, "completion_tokens": 1})],
        ]
        for items in cases:
            with self.subTest(items=items):
                aggregator = AnthropicNonStreamAggregator(self.context)
                with self.assertRaises(UpstreamProtocolViolation):
                    for item in items:
                        aggregator.process_event(item)
                    aggregator.finalize()


class AnthropicStreamResponseTests(unittest.TestCase):
    def setUp(self):
        self.context = AnthropicResponseContext("msg_stream", "req_stream", "anthropic/codebuddy/glm")

    def test_text_stream_sequence(self):
        encoder = AnthropicStreamEncoder(self.context)
        chunks = []
        chunks += encoder.process_event(event({"content": "hello"}))
        chunks += encoder.process_event(event({"content": " world"}, finish="stop"))
        chunks += encoder.process_event(event(usage={"prompt_tokens": 3, "completion_tokens": 2}))
        chunks += encoder.finalize()

        decoded = decode_sse(chunks)
        self.assertEqual([name for name, _ in decoded], [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ])
        self.assertEqual(decoded[0][1]["message"]["usage"], {"input_tokens": 0, "output_tokens": 0})
        self.assertEqual(decoded[2][1]["delta"], {"type": "text_delta", "text": "hello"})
        self.assertEqual(decoded[-2][1], {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"input_tokens": 3, "output_tokens": 2},
        })

    def test_content_filter_without_usage_finishes_instead_of_emitting_error(self):
        encoder = AnthropicStreamEncoder(self.context)
        chunks = encoder.process_event(event({}, finish="content_filter"))
        chunks += encoder.finalize()

        decoded = decode_sse(chunks)
        self.assertEqual(decoded[-2][1], {
            "type": "message_delta",
            "delta": {"stop_reason": "refusal", "stop_sequence": None},
            "usage": {"input_tokens": 0, "output_tokens": 0},
        })
        self.assertEqual(decoded[-1][0], "message_stop")

    def test_thinking_signature_switches_and_reopened_text(self):
        encoder = AnthropicStreamEncoder(self.context)
        chunks = []
        chunks += encoder.process_event(event({"reasoning_content": "think"}))
        chunks += encoder.process_event(event({"content": "answer"}))
        chunks += encoder.process_event(event({"reasoning_content": "again"}))
        chunks += encoder.process_event(event({"content": "tail"}, finish="stop"))
        chunks += encoder.process_event(event(usage={"prompt_tokens": 1, "completion_tokens": 4}))
        chunks += encoder.finalize()

        decoded = decode_sse(chunks)
        signatures = [data["delta"]["signature"] for name, data in decoded if name == "content_block_delta" and data["delta"]["type"] == "signature_delta"]
        self.assertEqual(signatures, [
            anthropic_thinking_signature("think"),
            anthropic_thinking_signature("again"),
        ])
        starts = [data["content_block"]["type"] for name, data in decoded if name == "content_block_start"]
        self.assertEqual(starts, ["thinking", "text", "thinking", "text"])

    def test_tool_arguments_can_arrive_before_metadata_and_parallel_tools_interleave(self):
        encoder = AnthropicStreamEncoder(self.context)
        chunks = []
        chunks += encoder.process_event(event({"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"x\":"}},
            {"index": 1, "id": "b", "function": {"name": "second", "arguments": "{\"b\":"}},
        ]}))
        chunks += encoder.process_event(event({"tool_calls": [
            {"index": 1, "function": {"arguments": "2}"}},
            {"index": 0, "id": "a", "function": {"name": "first", "arguments": "1}"}},
        ]}, finish="tool_calls"))
        chunks += encoder.process_event(event(usage={"prompt_tokens": 8, "completion_tokens": 3}))
        chunks += encoder.finalize()

        decoded = decode_sse(chunks)
        starts = [(data["index"], data["content_block"]) for name, data in decoded if name == "content_block_start"]
        self.assertEqual(starts, [
            (0, {"type": "tool_use", "id": "b", "name": "second", "input": {}}),
            (1, {"type": "tool_use", "id": "a", "name": "first", "input": {}}),
        ])
        partials = [data["delta"]["partial_json"] for name, data in decoded if name == "content_block_delta"]
        self.assertEqual(partials, ['{"b":', '2}', '{"x":1}'])
        self.assertEqual([data["index"] for name, data in decoded if name == "content_block_stop"], [0, 1])

    def test_stream_protocol_failures(self):
        operations = [
            lambda encoder: encoder.process_event(event({"reasoning_content": 1})),
            lambda encoder: encoder.process_event(event({"tool_calls": [{"index": 0, "id": "x", "function": {"name": "a", "arguments": "[]"}}]}, finish="tool_calls")) + encoder.process_event(event(usage={"prompt_tokens": 1, "completion_tokens": 1})) + encoder.finalize(),
            lambda encoder: encoder.process_event(event({"content": "x"}, finish="unknown")),
            lambda encoder: encoder.process_event(event({"content": "x"}, finish="stop")) + encoder.finalize(),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(UpstreamProtocolViolation):
                operation(AnthropicStreamEncoder(self.context))

    def test_remaining_upstream_tool_and_state_failures(self):
        invalid_deltas = [
            {"tool_calls": [1]},
            {"tool_calls": [{"index": True, "id": "x", "function": {"name": "a", "arguments": "{}"}}]},
            {"tool_calls": [{"function": {"arguments": "{}"}}]},
            {"tool_calls": [{"index": 0, "type": "computer", "id": "x", "function": {"name": "a", "arguments": "{}"}}]},
            {"tool_calls": [{"index": 0, "id": 1, "function": {"name": "a", "arguments": "{}"}}]},
            {"tool_calls": [{"index": 0, "id": "x", "function": []}]},
            {"tool_calls": [{"index": 0, "id": "x", "function": {"name": 1, "arguments": "{}"}}]},
            {"tool_calls": [{"index": 0, "id": "x", "function": {"name": "a", "arguments": {}}}]},
        ]
        for delta in invalid_deltas:
            with self.subTest(delta=delta), self.assertRaises(UpstreamProtocolViolation):
                AnthropicStreamEncoder(self.context).process_event(event(delta))

        changed_id = AnthropicStreamEncoder(self.context)
        changed_id.process_event(event({"tool_calls": [{
            "index": 0, "id": "a", "function": {"name": "tool", "arguments": ""},
        }]}))
        with self.assertRaises(UpstreamProtocolViolation):
            changed_id.process_event(event({"tool_calls": [{"index": 0, "id": "b"}]}))

        changed_name = AnthropicStreamEncoder(self.context)
        changed_name.process_event(event({"tool_calls": [{
            "index": 0, "id": "a", "function": {"name": "one", "arguments": ""},
        }]}))
        with self.assertRaises(UpstreamProtocolViolation):
            changed_name.process_event(event({"tool_calls": [{
                "index": 0, "function": {"name": "two"},
            }]}))

        changed_finish = AnthropicStreamEncoder(self.context)
        changed_finish.process_event(event({}, finish="stop"))
        with self.assertRaises(UpstreamProtocolViolation):
            changed_finish.process_event(event({}, finish="length"))

        late_metadata = AnthropicStreamEncoder(self.context)
        late_metadata.process_event(event({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}))
        late_metadata.process_event(event({}, finish="tool_calls"))
        late_metadata.process_event(event(usage={"prompt_tokens": 1, "completion_tokens": 1}))
        with self.assertRaises(UpstreamProtocolViolation):
            late_metadata.finalize()

        closed_tool = AnthropicStreamEncoder(self.context)
        closed_tool.process_event(event({"tool_calls": [{
            "index": 0, "id": "a", "function": {"name": "tool", "arguments": "{}"},
        }]}))
        closed_tool.process_event(event({"content": "after"}))
        with self.assertRaises(UpstreamProtocolViolation):
            closed_tool.process_event(event({"tool_calls": [{"index": 0, "function": {"arguments": ""}}]}))

    def test_empty_deltas_full_tool_metadata_and_repeated_finalize(self):
        encoder = AnthropicStreamEncoder(self.context)
        chunks = encoder.process_event(event({"content": "", "reasoning_content": None, "tool_calls": []}))
        self.assertEqual(decode_sse(chunks)[0][0], "message_start")
        chunks = encoder.process_event(event({"tool_calls": [{
            "index": 0, "id": "a", "function": {"name": "tool"},
        }]}))
        self.assertEqual(decode_sse(chunks)[0][1]["content_block"]["type"], "tool_use")
        encoder.process_event(event({"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}, finish="tool_calls"))
        encoder.process_event(event(usage={"prompt_tokens": 1, "completion_tokens": 1}))
        encoder.finalize()
        with self.assertRaises(UpstreamProtocolViolation):
            encoder.finalize()
        with self.assertRaises(UpstreamProtocolViolation):
            encoder.process_event(event({"content": "late"}))


class AnthropicResponseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.context = AnthropicResponseContext("msg_adapter", "req_adapter", "model")
        self.adapter = AnthropicDownstreamAdapter(self.context)

    def test_adapter_ignores_non_events_and_formats_stream_errors(self):
        stream_state = self.adapter.create_stream_state()
        self.assertEqual(self.adapter.process_stream_event(stream_state, "ping"), [])
        aggregator = self.adapter.create_non_stream_aggregator()
        self.assertIsNone(self.adapter.process_non_stream_event(aggregator, "ping"))
        self.assertEqual(self.adapter.stream_headers["request-id"], "req_adapter")
        error_chunk = self.adapter.format_stream_error(
            SimpleNamespace(status_code=504, error_type="upstream_timeout", message="late")
        )
        self.assertIn("event: error", error_chunk)
        self.assertIn('"request_id":"req_adapter"', error_chunk)
        self.assertEqual(
            format_anthropic_stream_error("req", "api_error", "bad").splitlines()[0],
            "event: error",
        )

    def test_adapter_finalize_methods(self):
        stream_state = self.adapter.create_stream_state()
        stream_state.process_event(event({"content": "ok"}, finish="stop"))
        stream_state.process_event(event(usage={"prompt_tokens": 1, "completion_tokens": 1}))
        self.assertEqual(
            decode_sse(self.adapter.finalize_stream(stream_state, True))[-1][0],
            "message_stop",
        )
        aggregator = self.adapter.create_non_stream_aggregator()
        self.adapter.process_non_stream_event(aggregator, event({"content": "ok"}, finish="stop"))
        self.adapter.process_non_stream_event(
            aggregator,
            event(usage={"prompt_tokens": 1, "completion_tokens": 1}),
        )
        self.assertEqual(self.adapter.finalize_non_stream(aggregator, True)["type"], "message")

    def test_error_mapping_matrix_and_usage_object_validation(self):
        cases = [
            (SimpleNamespace(status_code=429, upstream_status_code=None, error_type="x", detail="rate"), (429, "rate_limit_error", "rate")),
            (SimpleNamespace(status_code=502, upstream_status_code=429, error_type="x", message="rate"), (429, "rate_limit_error", "rate")),
            (SimpleNamespace(status_code=502, upstream_status_code=529, error_type="x", message="busy"), (529, "overloaded_error", "busy")),
            (SimpleNamespace(status_code=502, upstream_status_code=504, error_type="x", message="late"), (504, "timeout_error", "late")),
            (SimpleNamespace(status_code=403, upstream_status_code=None, error_type="x", message="no"), (403, "permission_error", "no")),
            (SimpleNamespace(status_code=502, upstream_status_code=403, error_type="x", message="no"), (403, "permission_error", "no")),
            (SimpleNamespace(status_code=413, upstream_status_code=None, error_type="x", message="big"), (413, "request_too_large", "big")),
            (SimpleNamespace(status_code=404, upstream_status_code=None, error_type="x", message="missing"), (404, "not_found_error", "missing")),
            (SimpleNamespace(status_code=400, upstream_status_code=None, error_type="x", message="bad"), (400, "invalid_request_error", "bad")),
            (SimpleNamespace(status_code=502, upstream_status_code=None, error_type="no_credential", message="none"), (500, "api_error", "none")),
            (SimpleNamespace(status_code=500, upstream_status_code=None, error_type="x", message="oops"), (500, "api_error", "oops")),
            (RuntimeError("raw"), (500, "api_error", "raw")),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(map_anthropic_error(error), expected)
        with self.assertRaises(UpstreamProtocolViolation):
            map_usage([])

    def test_tool_state_accepts_metadata_only_and_rejects_bad_json(self):
        state = response_module._ToolState(0)
        self.assertEqual(state.merge({"id": "a"}), "")
        self.assertEqual(state.merge({"function": {"name": ""}}), "")
        self.assertEqual(state.merge({"function": {"name": "tool"}}), "")
        state.merge({"function": {"arguments": "{"}})
        with self.assertRaises(UpstreamProtocolViolation):
            state.parsed_input()


class AnthropicServiceAdapterIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = AnthropicResponseContext("msg_service", "req_service", "model")
        self.adapter = AnthropicDownstreamAdapter(self.context)

    @staticmethod
    def _sse(value):
        payload = value if isinstance(value, str) else json.dumps(value)
        return f"data: {payload}\n\n"

    def _service(self, chunks):
        client = FakeHttpClient(chunks)
        return CodeBuddyStreamService(
            http_client_factory=mock.AsyncMock(return_value=client),
            api_url_factory=lambda: "https://upstream.invalid",
        )

    async def test_non_stream_protocol_violation_is_wrapped(self):
        service = self._service([
            self._sse({"choices": [{"delta": {"content": 1}, "finish_reason": "stop"}]}),
        ])
        with self.assertRaises(UpstreamAPIError) as raised:
            await service.handle_non_stream_response(
                {"model": "model"},
                {},
                response_adapter=self.adapter,
            )
        self.assertEqual(raised.exception.error_type, "upstream_protocol_error")

    async def test_stream_protocol_violation_before_and_after_first_chunk(self):
        before = await self._service([
            self._sse({"choices": [{"delta": {"content": 1}, "finish_reason": "stop"}]}),
        ]).handle_stream_response({"model": "model"}, {}, response_adapter=self.adapter)
        with self.assertRaises(UpstreamAPIError) as raised:
            await anext(before.body_iterator)
        self.assertEqual(raised.exception.error_type, "upstream_protocol_error")

        after = await self._service([
            self._sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]}),
            self._sse({"choices": [{"delta": {"content": 1}, "finish_reason": "stop"}]}),
        ]).handle_stream_response({"model": "model"}, {}, response_adapter=self.adapter)
        chunks = [chunk async for chunk in after.body_iterator]
        self.assertTrue(any("event: error" in chunk for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
