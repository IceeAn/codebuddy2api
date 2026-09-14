"""OpenAI 扩展请求与流式、非流式等价契约。"""

import tests  # 在生产模块导入前隔离测试数据目录。
import base64
import copy
import json
import unittest

from fastapi import HTTPException

from src.codebuddy_events import CodeBuddyResponseEvent
from src.codebuddy_events import UpstreamProtocolViolation
from src.openai_compat import CompletionResponseContext, OpenAIStreamNormalizer
from src.request_processor import RequestProcessor
from src.stream_service import OpenAIDownstreamAdapter, StreamResponseAggregator


def event(delta=None, **fields):
    return CodeBuddyResponseEvent.parse({
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": None}],
        **fields,
    })


class OpenAIRequestContractTests(unittest.TestCase):
    def test_multimodal_history_and_unknown_fields_survive_preparation(self):
        body = {"model": "kimi-k3-1", "messages": [
            {"role": "developer", "content": [{"type": "text", "text": "遵守要求"}]},
            {"role": "user", "content": [
                {"type": "text", "text": "查看图片"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "detail": "high"}},
                {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
                {"type": "input_audio", "input_audio": {"data": "AA==", "format": "wav"}},
                {"type": "file", "file": {"file_id": "file-test"}},
                {"type": "file", "file": {"filename": "test.txt", "file_data": "AA=="}},
                {"type": "future", "opaque": {"value": 1}},
            ]},
            {"role": "assistant", "function_call": {"name": "lookup", "arguments": "{}"}},
            {"role": "function", "name": "lookup", "content": "result"},
            {"role": "assistant", "content": None, "audio": {"id": "audio-test"}},
        ], "response_format": {"type": "json_object"}, "future": {"value": 1}}
        original = copy.deepcopy(body)
        RequestProcessor.validate_request(body)
        prepared = RequestProcessor.prepare_request(body)
        self.assertEqual(prepared.payload["messages"], body["messages"])
        self.assertEqual(prepared.payload["future"], body["future"])
        self.assertEqual(body, original)

    def test_token_alias_and_client_usage_are_independent_of_upstream(self):
        for fields, expected in [
            ({"max_completion_tokens": 12}, 12),
            ({"max_tokens": 12}, 12),
            ({"max_tokens": 12, "max_completion_tokens": 12}, 12),
            ({"max_tokens": None, "max_completion_tokens": 12}, 12),
            ({"max_completion_tokens": None}, None),
        ]:
            for include in (True, False):
                body = {"messages": [{"role": "user", "content": "hi"}],
                        "stream": True, "stream_options": {"include_usage": include}, **fields}
                with self.subTest(fields=fields, include=include):
                    RequestProcessor.validate_request(body)
                    prepared = RequestProcessor.prepare_request(body)
                    self.assertEqual(prepared.payload.get("max_tokens"), expected)
                    self.assertNotIn("max_completion_tokens", prepared.payload)
                    self.assertEqual(prepared.client_include_usage, include)
                    self.assertTrue(prepared.payload["stream_options"]["include_usage"])

    def test_invalid_handled_fields_fail_without_echoing_input(self):
        invalid = [
            {"max_tokens": 2, "max_completion_tokens": 3},
            *({key: value} for key in ("max_tokens", "max_completion_tokens")
              for value in (True, False, 0, -1, 1.5, "secret")),
            {"stream": "secret"}, {"stream_options": []},
            {"stream_options": {"include_usage": "secret"}},
        ]
        for fields in invalid:
            body = {"messages": [{"role": "user", "content": "hi"}], **fields}
            with self.subTest(fields=fields), self.assertRaises(HTTPException) as caught:
                RequestProcessor.validate_request(body)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertNotIn("secret", str(caught.exception.detail))

    def test_malformed_known_content_blocks_fail_but_unknown_types_pass(self):
        for part in [None, "secret", {}, {"type": 1}, {"type": "text", "text": 1},
                     {"type": "image_url", "image_url": "secret"},
                     {"type": "image_url", "image_url": {}},
                     {"type": "image_url", "image_url": {"url": "x", "detail": "secret"}},
                     {"type": "input_audio", "input_audio": {}},
                     {"type": "file", "file": {}}]:
            with self.subTest(part=part), self.assertRaises(HTTPException):
                RequestProcessor.validate_request({"messages": [{"role": "user", "content": [part]}]})


class OpenAIResponseContractTests(unittest.TestCase):
    def setUp(self):
        self.context = CompletionResponseContext("chatcmpl-test", 1, "test")

    def test_aggregates_standard_fields_without_discarding_data(self):
        aggregator = StreamResponseAggregator(self.context)
        adapter = OpenAIDownstreamAdapter(self.context)
        state = adapter.create_stream_state()
        streamed = []
        for text in ("a", "b"):
            upstream = event(choices=[{"index": 0, "delta": {
                "refusal": text,
                "annotations": [{"type": "url_citation", "url_citation": {"title": text}}],
                "function_call": {"name": "legacy", "arguments": text},
                "audio": {"id": "audio-test", "expires_at": 10,
                          "transcript": text, "data": base64.b64encode(text.encode()).decode()},
                "tool_calls": [{"index": 2, "id": "custom-test", "type": "custom",
                                "custom": {"name": "custom", "input": text}}],
            }, "logprobs": {"content": [{"token": text}], "refusal": [{"token": text}]},
               "finish_reason": "stop"}], service_tier="default")
            original = copy.deepcopy(upstream.chunk_data)
            aggregator.process_event(upstream)
            streamed.extend(json.loads(chunk[6:]) for chunk in adapter.process_stream_event(state, upstream))
            self.assertEqual(upstream.chunk_data, original)
        result = aggregator.finalize()
        choice = result["choices"][0]
        message = choice["message"]
        self.assertIsNone(message["content"])
        self.assertEqual(message["refusal"], "ab")
        self.assertEqual(message["function_call"]["arguments"], "ab")
        self.assertEqual(message["tool_calls"][0]["custom"]["input"], "ab")
        self.assertEqual(message["audio"]["transcript"], "ab")
        self.assertEqual(base64.b64decode(message["audio"]["data"]), b"ab")
        self.assertEqual(len(message["annotations"]), 2)
        self.assertEqual(len(choice["logprobs"]["content"]), 2)
        self.assertEqual(len(choice["logprobs"]["refusal"]), 2)
        self.assertEqual(result["service_tier"], "default")
        deltas = [chunk["choices"][0]["delta"] for chunk in streamed]
        self.assertEqual("".join(d.get("refusal", "") for d in deltas), message["refusal"])
        self.assertEqual(b"".join(base64.b64decode(d["audio"]["data"]) for d in deltas if "audio" in d),
                         base64.b64decode(message["audio"]["data"]))
        self.assertEqual("".join(d["function_call"]["arguments"] for d in deltas if "function_call" in d),
                         message["function_call"]["arguments"])
        self.assertEqual("".join(t["custom"]["input"] for d in deltas for t in d.get("tool_calls", [])),
                         message["tool_calls"][0]["custom"]["input"])
        self.assertEqual(sum(len(c["choices"][0].get("logprobs", {}).get("content", [])) for c in streamed), 2)

    def test_explicit_empty_text_stays_empty(self):
        aggregator = StreamResponseAggregator(self.context)
        aggregator.process_event(event({"content": "", "refusal": "no"}))
        self.assertEqual(aggregator.finalize()["choices"][0]["message"]["content"], "")

    def test_audio_metadata_only_fragments_and_invalid_base64(self):
        aggregator = StreamResponseAggregator(self.context)
        aggregator.process_event(event({"audio": {"id": "audio-test"}}))
        aggregator.process_event(event({"audio": {"expires_at": 10}}))
        aggregator.process_event(event({"audio": {"data": "YQ=="}}))
        aggregator.process_event(event({"audio": {"data": "Yg==", "transcript": "ab"}}))
        message = aggregator.finalize()["choices"][0]["message"]
        self.assertEqual(message["audio"], {"id": "audio-test", "expires_at": 10,
                                            "data": "YWI=", "transcript": "ab"})
        for value in ("!secret", None, "é"):
            with self.subTest(value=value), self.assertRaises(UpstreamProtocolViolation) as caught:
                aggregator.process_event(event({"audio": {"data": value}}))
            self.assertNotIn("secret", str(caught.exception))

    def test_logprobs_null_then_data_and_trailing_null_preserve_tokens(self):
        aggregator = StreamResponseAggregator(self.context)
        for probabilities in ({"content": None}, {"content": [{"token": "a"}]}, {"content": None}):
            aggregator.process_event(event(choices=[{"delta": {}, "logprobs": probabilities}]))
        self.assertEqual(aggregator.finalize()["choices"][0]["logprobs"], {
            "content": [{"token": "a"}], "refusal": None,
        })

    def test_tool_type_change_fails_and_metadata_only_keeps_id(self):
        aggregator = StreamResponseAggregator(self.context)
        aggregator.process_event(event({"tool_calls": [{"index": 0, "type": "custom", "id": "original"}]}))
        aggregator.process_event(event({"tool_calls": [{"index": 0, "custom": {"name": "run", "input": "a"}}]}))
        aggregator.process_event(event({"tool_calls": [{"index": 0, "id": "updated"}]}))
        call = aggregator.finalize()["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call, {"id": "updated", "type": "custom", "custom": {"name": "run", "input": "a"}})
        with self.assertRaises(UpstreamProtocolViolation):
            aggregator.process_event(event({"tool_calls": [{"index": 0, "function": {"name": "f"}}]}))

    def test_empty_tool_metadata_preserves_known_call_and_unknown_kind_is_ignored(self):
        aggregator = StreamResponseAggregator(self.context)
        aggregator.process_event(event({"tool_calls": [
            {"index": 0, "id": "function-test", "type": "function"},
            {"index": 0},
            {"index": 1, "id": "unknown-test", "type": "future"},
        ]}))
        aggregator.process_event(event({"tool_calls": [{"index": 0, "function": {"name": "run", "arguments": "{}"}}]}))
        calls = aggregator.finalize()["choices"][0]["message"]["tool_calls"]
        self.assertEqual(calls, [{"id": "function-test", "type": "function", "function": {"name": "run", "arguments": "{}"}}])

    def test_only_probability_delta_is_not_dropped_and_missing_usage_not_fabricated(self):
        chunk = event(choices=[{"index": 0, "delta": {}, "logprobs": {"content": [{"token": "x"}]}}]).chunk_data
        self.assertEqual(OpenAIStreamNormalizer().normalize(chunk), [
            {"choices": [{"index": 0, "delta": {}, "logprobs": {"content": [{"token": "x"}]}, "finish_reason": None}]},
        ])
        adapter = OpenAIDownstreamAdapter(self.context, include_usage=True)
        state = adapter.create_stream_state()
        self.assertEqual(adapter.finalize_stream(state, True), ["data: [DONE]\n\n"])

    def test_role_and_reasoning_split_do_not_duplicate_logprobs(self):
        raw = {"choices": [{"index": 0, "delta": {"reasoning_content": "think", "content": "answer"},
                            "logprobs": {"content": [{"token": "answer"}]}, "finish_reason": "stop"}]}
        chunks = OpenAIStreamNormalizer().normalize(raw)
        self.assertEqual([c["choices"][0]["delta"] for c in chunks], [
            {"role": "assistant"}, {"reasoning_content": "think"}, {"content": "answer"},
        ])
        self.assertEqual(sum(bool(c["choices"][0].get("logprobs")) for c in chunks), 1)
        for delta in ({"refusal": "no"}, {"function_call": {"name": "f"}}, {"audio": {"id": "a"}}):
            self.assertEqual(OpenAIStreamNormalizer().normalize(event(delta).chunk_data)[0]["choices"][0]["delta"],
                             {"role": "assistant"})

    def test_client_usage_controls_only_downstream_and_emits_one_final_chunk(self):
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        for include in (True, False):
            adapter = OpenAIDownstreamAdapter(self.context, include_usage=include)
            state = adapter.create_stream_state()
            output = adapter.process_stream_event(state, event({"content": "answer"}, usage=usage))
            output += adapter.process_stream_event(state, event(choices=[], usage=usage))
            output += adapter.finalize_stream(state, True)
            data = [json.loads(s[6:]) for s in output if "[DONE]" not in s]
            if include:
                self.assertEqual(data[-1]["choices"], [])
                self.assertEqual(data[-1]["usage"], usage)
                self.assertTrue(all(c["usage"] is None for c in data[:-1]))
            else:
                self.assertTrue(all("usage" not in c for c in data))
            self.assertEqual(output[-1], "data: [DONE]\n\n")

    def test_terminal_usage_metadata_survives_without_changing_the_envelope(self):
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
        adapter = OpenAIDownstreamAdapter(self.context, include_usage=True)
        state = adapter.create_stream_state()
        aggregator = adapter.create_non_stream_aggregator()
        terminal = event(choices=[], usage=usage, system_fingerprint="fp_terminal", service_tier="default",
                         extension={"values": [1]}, id="upstream-id", object="upstream-object", created=0, model="upstream-model")
        original = copy.deepcopy(terminal.chunk_data)
        output = []
        for item in (event({"content": "answer"}), terminal):
            output.extend(adapter.process_stream_event(state, item))
            aggregator.process_event(item)
        self.assertEqual(terminal.chunk_data, original)
        terminal.chunk_data["extension"]["values"].append(2)
        # 后续空 usage 不应覆盖已观测计数，元数据按各字段最近一次出现的值保留。
        output.extend(adapter.process_stream_event(state, event(choices=[], usage=None, service_tier="priority")))
        output.extend(adapter.finalize_stream(state, True))
        chunks = [json.loads(item[6:]) for item in output[:-1]]
        final = chunks[-1]
        self.assertEqual(sum(chunk.get("usage") is not None for chunk in chunks), 1)
        self.assertEqual(final["usage"], usage)
        self.assertEqual(final["choices"], [])
        self.assertEqual(final["system_fingerprint"], aggregator.finalize()["system_fingerprint"])
        self.assertEqual(final["service_tier"], "priority")
        self.assertEqual(final["extension"], {"values": [1]})
        self.assertEqual((final["id"], final["created"], final["model"], final["object"]),
                         (self.context.response_id, self.context.created, self.context.model, "chat.completion.chunk"))
        self.assertEqual(output[-1], "data: [DONE]\n\n")
