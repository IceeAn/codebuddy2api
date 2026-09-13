"""Responses 请求转换与 Codex 工具往返契约。"""
import copy
import json
import unittest

from src.openai_errors import OpenAIRequestError
from src.responses_request import translate_responses_request, tool_alias


class ResponsesRequestTests(unittest.TestCase):
    def translate(self, **fields):
        return translate_responses_request({"model": "kimi-k3-1", "input": "你好", **fields})

    def test_text_options_and_private_fields(self):
        payload, bindings = self.translate(
            instructions="系统规则", stream=True, max_output_tokens=100,
            reasoning={"effort": "high", "summary": "auto"},
            text={"format": {"type": "json_schema", "name": "answer", "schema": {"type": "object"}}},
            metadata={"private": "secret"}, client_metadata={"session_id": "secret"},
            prompt_cache_key="secret", include=["reasoning.encrypted_content"], extra="secret",
        )
        self.assertEqual(payload["messages"], [{"role": "system", "content": "系统规则"}, {"role": "user", "content": "你好"}])
        self.assertEqual(payload["reasoning_effort"], "high")
        self.assertEqual(payload["max_tokens"], 100)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["response_format"]["json_schema"]["name"], "answer")
        self.assertNotIn("secret", json.dumps(payload))
        self.assertEqual(bindings, {})

    def test_custom_namespace_and_history_without_declarations(self):
        tool = {"type": "namespace", "name": "editor", "description": "编辑器", "tools": [
            {"type": "custom", "name": "patch", "format": {"type": "grammar", "syntax": "lark", "definition": 'start: "x"'}}]}
        payload, bindings = self.translate(tools=[tool])
        alias = tool_alias("patch", "editor")
        self.assertEqual(payload["tools"][0]["function"]["name"], alias)
        self.assertIn('start: "x"', payload["tools"][0]["function"]["description"])
        self.assertEqual(bindings[alias].kind, "custom")
        self.assertEqual(bindings[alias].namespace, "editor")
        history = [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "考虑"}]},
            {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "修改"}]},
            {"type": "custom_tool_call", "name": "patch", "namespace": "editor", "call_id": "original", "input": "x\n\\\""},
            {"type": "custom_tool_call_output", "call_id": "original", "output": ""},
        ]
        original = copy.deepcopy(history)
        payload, _ = self.translate(input=history)
        assistant, result = payload["messages"]
        self.assertEqual(assistant["reasoning_content"], "考虑")
        self.assertEqual(assistant["tool_calls"][0]["id"], "original")
        self.assertEqual(json.loads(assistant["tool_calls"][0]["function"]["arguments"]), {"input": "x\n\\\""})
        self.assertEqual(result, {"role": "tool", "tool_call_id": "original", "content": ""})
        self.assertEqual(history, original)

    def test_image_and_tool_result_order(self):
        image = {"type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "high"}
        payload, _ = self.translate(input=[
            {"role": "user", "content": [{"type": "input_text", "text": "看"}, image]},
            {"type": "function_call", "call_id": "a", "name": "read", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "a", "output": [image, {"type": "input_text", "text": "完成"}]},
        ])
        self.assertEqual(payload["messages"][0]["content"][1], {"type": "image_url", "image_url": {"url": image["image_url"], "detail": "high"}})
        self.assertEqual(payload["messages"][2]["content"][0]["type"], "image_url")

    def test_tool_search_discovers_namespace_tools(self):
        found = {"type": "namespace", "name": "mcp", "tools": [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]}
        payload, bindings = self.translate(
            tools=[{"type": "tool_search", "execution": "client", "parameters": {"type": "object"}}],
            input=[{"type": "tool_search_call", "execution": "client", "call_id": "search", "arguments": {"query": "lookup"}},
                   {"type": "tool_search_output", "execution": "client", "call_id": "search", "tools": [found]},
                   {"role": "user", "content": "继续"}],
        )
        self.assertEqual(len(bindings), 2)
        self.assertEqual(payload["messages"][1]["role"], "tool")
        self.assertIn(tool_alias("lookup", "mcp"), bindings)

    def test_choice_filters_upstream_definitions(self):
        tools = [{"type": "function", "name": name, "parameters": {}} for name in ("a", "b")]
        payload, bindings = self.translate(tools=tools, tool_choice={"type": "function", "name": "b"})
        self.assertEqual(payload["tool_choice"], "required")
        self.assertEqual([x["function"]["name"] for x in payload["tools"]], ["b"])
        self.assertEqual(set(bindings), {"b"})

    def test_known_unsupported_semantics_fail_before_upstream(self):
        cases = [
            {"store": True}, {"previous_response_id": "resp_x"}, {"conversation": "conv_x"},
            {"background": True}, {"context_management": [{"type": "compaction"}]},
            {"tools": [{"type": "web_search"}]},
            {"tools": [{"type": "tool_search", "execution": "server"}]},
            {"input": [{"type": "item_reference", "id": "x"}]},
            {"input": [{"type": "reasoning", "summary": [], "encrypted_content": "x"}]},
            {"input": [{"role": "user", "content": [{"type": "input_image", "file_id": "file_x"}]}]},
            {"input": [{"type": "function_call_output", "call_id": "missing", "output": "x"}]},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(OpenAIRequestError):
                self.translate(**case)

    def test_optional_options_and_reasoning_replay(self):
        payload, _ = self.translate(input=[
            {"type": "reasoning", "summary": []},
            {"role": "developer", "content": "规则"},
            {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "原文"}], "summary": [{"type": "summary_text", "text": "摘要"}]},
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "后续"}]},
            {"role": "assistant", "content": [{"type": "refusal", "refusal": "拒绝"}]},
            {"role": "assistant", "content": "第二条"},
            {"role": "user", "content": [{"type": "input_image", "image_url": "https://example.org/a.png", "detail": "original"}]},
        ], temperature=0.2, top_p=0.9, parallel_tool_calls=False, reasoning={"effort": "none"},
            text={"format": {"type": "json_object"}, "verbosity": "low"}, store=False, background=False)
        self.assertEqual(payload["messages"][1]["reasoning_content"], "原文后续")
        self.assertEqual(payload["messages"][-1]["content"][0]["image_url"]["detail"], "high")
        self.assertFalse(payload["enable_thinking"])
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["top_p"], 0.9)
        self.assertFalse(payload["parallel_tool_calls"])
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["verbosity"], "low")
        self.assertEqual(self.translate(reasoning={"summary": "none"}, text={})[0]["stream"], False)
        self.assertTrue(self.translate(text={"format": {"type": "json_schema", "name": "x", "schema": {}, "strict": True, "description": "结构"}})[0]["response_format"]["json_schema"]["strict"])

    def test_custom_text_strict_allowed_tools_and_rediscovery(self):
        tool = {"type": "custom", "name": "patch", "format": {"type": "text"}, "strict": False}
        payload, _ = self.translate(tools=[tool, tool], tool_choice={"type": "allowed_tools", "mode": "auto", "tools": [{"type": "custom", "name": "patch"}]})
        self.assertIn("自由文本", payload["tools"][0]["function"]["description"])
        self.assertFalse(payload["tools"][0]["function"]["strict"])
        self.assertEqual(payload["tool_choice"], "auto")

    def test_invalid_field_shapes(self):
        for body in (None, [], "x", {}, {"model": "x"}):
            with self.subTest(body=body), self.assertRaises(OpenAIRequestError):
                translate_responses_request(body)
        cases = [
            {"input": 5}, {"input": [None]}, {"input": [{"role": "invalid", "content": "x"}]},
            {"input": [{"role": "user", "content": [None]}]},
            {"input": [{"role": "user", "content": [{"type": "input_file", "file_id": "x"}]}]},
            {"input": [{"role": "user", "content": [{"type": "input_text", "text": 1}]}]},
            {"input": [{"type": []}]}, {"instructions": False}, {"model": 2},
            {"tools": [None]}, {"tools": [{"type": "function", "name": "bad name"}]},
            {"tools": [{"type": "function", "name": "f", "strict": None}]},
            {"tools": [{"type": "function", "name": "f", "parameters": []}]},
            {"tools": [{"type": "namespace", "name": "a", "tools": [{"type": "namespace", "name": "b", "tools": []}]}]},
            {"tools": [{"type": "namespace", "name": "a", "tools": [{"type": "tool_search", "execution": "client"}]}]},
            {"tools": [{"type": "function", "name": "f"}, {"type": "custom", "name": "f"}]},
            *[{"tools": [{"type": "custom", "name": "f", "format": fmt}]} for fmt in ([], {"type": "bad"}, {"type": "grammar", "syntax": "bad"}, {"type": "grammar", "syntax": "lark", "definition": None})],
            {"input": [{"type": "reasoning", "summary": [{"type": "wrong", "text": "x"}]}]},
            {"input": [{"type": "function_call", "call_id": "x", "name": "f", "namespace": 1, "arguments": "{}"}]},
            {"tool_choice": "bad"}, {"tool_choice": []}, {"tool_choice": False},
            {"tool_choice": {"type": "function", "name": "missing"}},
            {"tool_choice": {"type": "allowed_tools", "tools": [], "mode": "none"}},
            {"tool_choice": {"type": "allowed_tools", "tools": []}},
            {"stream": "true"}, {"max_output_tokens": True}, {"store": 0}, {"background": 0},
            {"temperature": True}, {"top_p": float("nan")}, {"parallel_tool_calls": 0},
            {"reasoning": []}, {"reasoning": {"effort": "bad"}}, {"text": []},
            {"text": {"verbosity": "bad"}}, {"text": {"format": {"type": "bad"}}},
            {"text": {"format": {"type": "json_schema", "name": "x", "schema": {}, "strict": "true"}}},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(OpenAIRequestError):
                self.translate(**case)

    def test_duplicate_and_mismatched_results(self):
        call = {"type": "function_call", "call_id": "x", "name": "f", "arguments": "{}"}
        result = {"type": "function_call_output", "call_id": "x", "output": []}
        self.assertEqual(self.translate(input=[call, result])[0]["messages"][-1]["content"], "")
        for history in ([call, call], [call, result, result], [call, {**result, "type": "custom_tool_call_output"}]):
            with self.subTest(history=history), self.assertRaises(OpenAIRequestError):
                self.translate(input=history)
