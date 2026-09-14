"""Responses 事件状态机及 SDK 可消费输出。"""

import tests  # 在生产模块导入前隔离测试数据目录。
import json
import unittest

from src.codebuddy_events import CodeBuddyResponseEvent, UpstreamProtocolViolation
from src.responses_request import ToolBinding
from src.responses_response import ResponsesAdapter
from src.responses_response import response_usage


def event(delta=None, finish=None, usage=None):
    value = {"choices": [{"delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        value["usage"] = usage
    return CodeBuddyResponseEvent.parse(value)


def decode(wires):
    return [json.loads(wire.split("data: ", 1)[1]) for wire in wires]


class ResponsesResponseTests(unittest.TestCase):
    def test_truncated_tool_group_is_not_emitted_as_executable_calls(self):
        for finish in ("length", "content_filter"):
            for kind in ("function", "custom"):
                with self.subTest(finish=finish, kind=kind):
                    adapter = self.adapter({"tool": ToolBinding(kind, "tool")})
                    state = adapter.create_stream_state()
                    aggregate = adapter.create_non_stream_aggregator()
                    source = [event({"content": "保留已有文本"}), event({"tool_calls": [
                        {"index": 0, "id": "a", "function": {"name": "tool", "arguments": '{"input":"ok"}'}},
                        {"index": 1, "id": "b", "function": {"name": "tool", "arguments": '{"input":"截断'}},
                    ]}, finish)]
                    wires = []
                    for item in source:
                        wires += adapter.process_stream_event(state, item)
                        adapter.process_non_stream_event(aggregate, item)
                    wires += adapter.finalize_stream(state, True)
                    final = adapter.finalize_non_stream(aggregate, True)
                    events = decode(wires)
                    self.assertEqual(events[-1]["type"], "response.incomplete")
                    self.assertEqual(events[-1]["response"], final)
                    self.assertEqual([item["type"] for item in final["output"]], ["message"])
                    self.assertEqual(final["output"][0]["content"][0]["text"], "保留已有文本")
                    self.assertFalse(state.pending)
                    self.assertFalse(any("arguments" in item["type"] or "tool_call_input" in item["type"] for item in events))

    def adapter(self, bindings=None):
        return ResponsesAdapter("model", bindings or {}, response_id="resp_test", created_at=1)

    def test_stream_and_nonstream_agree(self):
        adapter = self.adapter({"patch": ToolBinding("custom", "patch")})
        state = adapter.create_stream_state()
        aggregate = adapter.create_non_stream_aggregator()
        source = [event({"reasoning_content": "思考", "content": "开始"}),
                  event({"content": "修改"}),
                  event({"tool_calls": [{"index": 1, "id": "call_a", "type": "function", "function": {"name": "patch", "arguments": '{"input":'}}]}),
                  event({"tool_calls": [{"index": 1, "function": {"arguments": '"x\\n\\\""}'}}]}, "tool_calls"),
                  event(usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13})]
        wires = []
        for value in source:
            wires += adapter.process_stream_event(state, value)
            adapter.process_non_stream_event(aggregate, value)
        wires += adapter.finalize_stream(state, True)
        events = decode(wires)
        final = adapter.finalize_non_stream(aggregate, True)
        self.assertEqual(events[-1]["response"], final)
        self.assertEqual(events[-1]["type"], "response.completed")
        self.assertEqual([x["sequence_number"] for x in events], list(range(len(events))))
        self.assertEqual([x["type"] for x in final["output"]], ["reasoning", "message", "custom_tool_call"])
        self.assertEqual(final["output"][-1]["call_id"], "call_a")
        self.assertEqual(final["output"][-1]["input"], 'x\n"')
        self.assertNotIn("[DONE]", "".join(wires))
        self.assertEqual(final["usage"]["input_tokens"], 10)

    def test_tools_sorted_and_namespace_restored(self):
        bindings = {"ns": ToolBinding("function", "read", "files"), "patch": ToolBinding("custom", "patch")}
        adapter = self.adapter(bindings)
        state = adapter.create_stream_state()
        events = decode(adapter.process_stream_event(state, event({"tool_calls": [
            {"index": 4, "id": "later", "function": {"name": "patch", "arguments": '{"input":"ok"}'}},
            {"index": 2, "id": "first", "function": {"name": "ns", "arguments": '{}'}},
        ]}, "tool_calls")) + adapter.finalize_stream(state, True))
        output = events[-1]["response"]["output"]
        self.assertEqual([x["call_id"] for x in output], ["first", "later"])
        self.assertEqual(output[0]["namespace"], "files")
        self.assertEqual(output[0]["name"], "read")

    def test_empty_name_fragments_preserve_tool_metadata(self):
        adapter = self.adapter({"patch": ToolBinding("custom", "patch")})
        state = adapter.create_stream_state()
        for function in ({"name": "", "arguments": None},
                         {"name": "patch", "arguments": '{"input":'},
                         {"name": "", "arguments": '"ok"}'}):
            adapter.process_stream_event(state, event({"tool_calls": [
                {"index": 0, "id": "original" if function["name"] == "patch" else "", "function": function}]}))
        adapter.process_stream_event(state, event(finish="tool_calls"))
        result = decode(adapter.finalize_stream(state, True))[-1]["response"]
        self.assertEqual(result["output"][0]["input"], "ok")

    def test_empty_legacy_function_placeholder_is_not_a_call(self):
        for placeholder in ({"name": "", "arguments": ""}, {"name": None, "arguments": None}, {}):
            adapter = self.adapter()
            state = adapter.create_stream_state()
            adapter.process_stream_event(state, event({"content": "完成", "function_call": placeholder}, "stop"))
            self.assertEqual(decode(adapter.finalize_stream(state, True))[-1]["type"], "response.completed")

    def test_incomplete_and_missing_usage(self):
        for reason, mapped in (("length", "max_output_tokens"), ("content_filter", "content_filter")):
            adapter = self.adapter()
            state = adapter.create_stream_state()
            events = decode(adapter.process_stream_event(state, event({"content": "部分"}, reason)) + adapter.finalize_stream(state, True))
            self.assertEqual(events[-1]["type"], "response.incomplete")
            self.assertEqual(events[-1]["response"]["incomplete_details"], {"reason": mapped})
            self.assertEqual(events[-1]["response"]["output"][0]["status"], "incomplete")
            self.assertIsNone(events[-1]["response"]["usage"])

    def test_truncated_or_invalid_tool_is_not_success(self):
        for source in [[], [event({"content": "部分"})], [event(finish="unexpected")],
                       [event({"tool_calls": [{"id": "x", "function": {"name": "patch", "arguments": "[]"}}]}, "tool_calls")]]:
            adapter = self.adapter({"patch": ToolBinding("custom", "patch")})
            state = adapter.create_stream_state()
            with self.assertRaises(UpstreamProtocolViolation):
                for item in source:
                    adapter.process_stream_event(state, item)
                adapter.finalize_stream(state, True)

    def test_failure_event_has_continuing_sequence(self):
        adapter = self.adapter()
        state = adapter.create_stream_state()
        initial = decode(adapter.process_stream_event(state, event({"content": "部分"})))
        failed = decode([adapter.format_stream_error(RuntimeError("private-secret"))])[0]
        self.assertEqual(failed["type"], "response.failed")
        self.assertEqual(failed["sequence_number"], initial[-1]["sequence_number"] + 1)
        self.assertNotIn("private-secret", json.dumps(failed))

    def test_refusal_empty_text_opaque_events_and_client_tool_search(self):
        adapter = self.adapter({"search": ToolBinding("tool_search", "cb2a_tool_search")})
        state = adapter.create_stream_state()
        self.assertEqual(adapter.process_stream_event(state, "opaque"), [])
        wires = adapter.process_stream_event(state, event({"content": "", "refusal": "拒绝"}))
        wires += adapter.process_stream_event(state, event({"refusal": "原因", "tool_calls": []}))
        wires += adapter.process_stream_event(state, event({"tool_calls": [{"index": 0, "id": "call_s"}]}))
        wires += adapter.process_stream_event(state, event({"tool_calls": [{"index": 0, "function": {"name": "search", "arguments": '{"query":"read"}'}}]}))
        wires += adapter.process_stream_event(state, event({"content": "已查找"}, "stop"))
        wires += adapter.finalize_stream(state, False)
        output = decode(wires)[-1]["response"]["output"]
        self.assertEqual(output[0]["content"][0], {"type": "refusal", "refusal": "拒绝原因"})
        self.assertEqual(output[1]["type"], "tool_search_call")
        self.assertEqual(output[1]["arguments"], {"query": "read"})
        self.assertEqual(output[1]["execution"], "client")
        with self.assertRaises(UpstreamProtocolViolation):
            adapter.process_stream_event(state, event())
        with self.assertRaises(UpstreamProtocolViolation):
            adapter.finalize_stream(state, True)
        timeout_adapter = self.adapter()
        timeout_adapter.create_stream_state()
        error = type("Error", (), {"status_code": 504})()
        self.assertIn("timed out", timeout_adapter.format_stream_error(error))

    def test_usage_preserves_observed_details_and_rejects_invalid_values(self):
        self.assertIsNone(response_usage(None))
        value = response_usage({"prompt_tokens": 10, "completion_tokens": 4,
                                "prompt_tokens_details": {"cached_tokens": 3},
                                "completion_tokens_details": {"reasoning_tokens": 2}})
        self.assertEqual(value["total_tokens"], 14)
        self.assertEqual(value["input_tokens_details"], {"cached_tokens": 3})
        self.assertEqual(value["output_tokens_details"], {"reasoning_tokens": 2})
        self.assertNotIn("input_tokens_details", response_usage({"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": {}}))
        for value in ([], {}, {"prompt_tokens": True, "completion_tokens": 1},
                      {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": -1},
                      {"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": []},
                      {"prompt_tokens": 1, "completion_tokens": 1, "completion_tokens_details": {"reasoning_tokens": None}}):
            with self.subTest(value=value), self.assertRaises(UpstreamProtocolViolation):
                response_usage(value)

    def test_malformed_upstream_tools_and_unsupported_content_fail(self):
        good = {"index": 0, "id": "a", "type": "function", "function": {"name": "patch", "arguments": '{"input":"x"}'}}
        sequences = [[event({field: value})] for field, value in (("audio", {"data": "x"}), ("function_call", {"name": "x"}), ("function_call", "invalid"), ("content", []), ("tool_calls", {}))]
        bad_calls = [None, {**good, "type": "custom"}, {**good, "index": -1}, {**good, "index": True},
                     {"function": {}}, {**good, "id": 1}, {**good, "function": []},
                     {**good, "function": {"name": 1}}, {**good, "function": {"name": "patch", "arguments": 1}},
                     {"index": 0, "function": good["function"]},
                     {**good, "function": {"name": "missing", "arguments": "{}"}}]
        sequences += [[event({"tool_calls": [call]}, "tool_calls")] for call in bad_calls]
        sequences += [[event({"tool_calls": [good]}), event({"tool_calls": [{"index": 0, "id": "changed"}]})],
                      [event({"tool_calls": [good]}), event({"tool_calls": [{"index": 0, "function": {"name": "changed"}}]})],
                      [event({"tool_calls": [good]}), event({"content": "边界"}), event({"tool_calls": [good]}, "tool_calls")]]
        for arguments in ('{"input":NaN}', '{bad', '[]', '{"input":1}', '{"input":"x","extra":true}'):
            sequences.append([event({"tool_calls": [{**good, "function": {"name": "patch", "arguments": arguments}}]}, "tool_calls")])
        for sequence in sequences:
            adapter = self.adapter({"patch": ToolBinding("custom", "patch")})
            state = adapter.create_stream_state()
            with self.subTest(sequence=sequence), self.assertRaises(UpstreamProtocolViolation):
                for item in sequence:
                    adapter.process_stream_event(state, item)
                adapter.finalize_stream(state, True)
