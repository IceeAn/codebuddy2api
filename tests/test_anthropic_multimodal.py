"""Anthropic 多模态、文档及可映射参数的请求契约。"""

import copy
import unittest
from unittest import mock

from src.anthropic_compat import AnthropicProtocolError, translate_anthropic_request
from src.request_processor import RequestProcessor


IMAGE = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="}}
URL_IMAGE = {"type": "image", "source": {"type": "url", "url": "https://example.com/test.png"}}
MAPPED_IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}}
MAPPED_URL = {"type": "image_url", "image_url": {"url": "https://example.com/test.png"}}
TEXT_DOCUMENT = {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "正文"}}


class AnthropicMultimodalTests(unittest.TestCase):
    def translate(self, content=None, **kwargs):
        body = {"model": "kimi-k3-1", "max_tokens": 256,
                "messages": [{"role": "user", "content": content if content is not None else "hello"}], **kwargs}
        original = copy.deepcopy(body)
        try:
            return translate_anthropic_request(body)
        finally:
            self.assertEqual(body, original)

    def test_images_preserve_order_and_history_without_forwarding_metadata(self):
        content = [IMAGE, {"type": "text", "text": "之间"}, {**URL_IMAGE, "cache_control": {"type": "ephemeral"}}]
        result = self.translate(messages=[{"role": "user", "content": content},
                                          {"role": "assistant", "content": "已看图"},
                                          {"role": "user", "content": [URL_IMAGE]}])
        self.assertEqual(result["messages"][0]["content"], [MAPPED_IMAGE, {"type": "text", "text": "之间"}, MAPPED_URL])
        self.assertEqual(result["messages"][2]["content"], [MAPPED_URL])
        for media_type in ("image/jpeg", "image/gif", "image/webp"):
            image = {"type": "image", "source": {**IMAGE["source"], "media_type": media_type}}
            self.assertEqual(self.translate([image])["messages"][0]["content"][0]["image_url"]["url"],
                             f"data:{media_type};base64,aW1hZ2U=")

    def test_documents_preserve_metadata_boundaries_and_mixed_content(self):
        document = {"type": "document", "title": "资料", "context": "上下文",
                    "citations": {"enabled": False}, "source": {"type": "content", "content": [IMAGE, {"type": "text", "text": "正文"}, URL_IMAGE]}}
        parts = self.translate([document, TEXT_DOCUMENT])["messages"][0]["content"]
        self.assertEqual(parts[:5], [{"type": "text", "text": "[文档开始]\n标题：资料\n上下文：上下文"},
                                    MAPPED_IMAGE, {"type": "text", "text": "正文"}, MAPPED_URL,
                                    {"type": "text", "text": "[文档结束]"}])
        self.assertEqual(parts[5:], [{"type": "text", "text": "[文档开始]"},
                                     {"type": "text", "text": "正文"}, {"type": "text", "text": "[文档结束]"}])
        plain = {"type": "document", "title": None, "context": "", "citations": {},
                 "source": {"type": "content", "content": "正文"}}
        self.assertEqual(self.translate([plain])["messages"][0]["content"][1], {"type": "text", "text": "正文"})

    def test_parallel_tool_results_keep_images_and_error_marker_in_the_tool(self):
        messages = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": name, "name": "read", "input": {}} for name in ("a", "b")]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "b", "content": [IMAGE, TEXT_DOCUMENT], "is_error": True},
                {"type": "tool_result", "tool_use_id": "a", "content": [URL_IMAGE]},
                IMAGE, {"type": "text", "text": "继续"}]},
        ]
        result = self.translate(messages=messages)["messages"]
        self.assertEqual([m["role"] for m in result], ["assistant", "tool", "tool", "user"])
        self.assertEqual([m["tool_call_id"] for m in result[1:3]], ["b", "a"])
        self.assertEqual(result[1]["content"][:2], [{"type": "text", "text": "[tool_error]\n"}, MAPPED_IMAGE])
        self.assertEqual(result[2]["content"], [MAPPED_URL])
        for leading in (IMAGE, TEXT_DOCUMENT):
            with self.subTest(leading=leading), self.assertRaises(AnthropicProtocolError):
                self.translate(messages=[messages[0], {"role": "user", "content": [leading, messages[1]["content"][0]]}])

    def test_invalid_media_and_documents_fail_without_echoing_data(self):
        sources = [None, [], {}, {"type": []}, {"type": "file", "file_id": "secret"},
                   {"type": "base64", "media_type": []}, {"type": "base64", "media_type": "image/svg+xml", "data": "secret"},
                   {"type": "base64", "media_type": "image/png", "data": ""}, {"type": "url", "url": 1}]
        blocks = [{"type": "image", "source": source} for source in sources]
        blocks += [{"type": "document", "source": source} for source in (
            None, [], {}, {"type": []}, {"type": "base64", "media_type": "application/pdf", "data": "secret"},
            {"type": "url", "url": "https://example.com/secret"}, {"type": "file", "file_id": "secret"},
            {"type": "text", "media_type": "application/json", "data": "secret"},
            {"type": "text", "media_type": "text/plain", "data": ""},
            {"type": "content", "content": []}, {"type": "content", "content": 1},
            {"type": "content", "content": [1]}, {"type": "content", "content": [TEXT_DOCUMENT]},
        )]
        blocks += [{**TEXT_DOCUMENT, key: value} for key, value in (
            ("title", 1), ("context", []), ("citations", []), ("citations", {"enabled": True}), ("citations", {"enabled": 1}))]
        blocks += [{"type": "future", "secret": "secret"}, {"type": []}]
        for block in blocks:
            with self.subTest(block=block), self.assertRaises(AnthropicProtocolError) as raised:
                self.translate([block])
            self.assertNotIn("secret", str(raised.exception))
        for role in ("assistant", "system"):
            with self.subTest(role=role), self.assertRaises(AnthropicProtocolError):
                self.translate(messages=[{"role": role, "content": [IMAGE]}])

    def test_mapped_options_and_policy_precedence(self):
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}
        for effort in ("low", "medium", "high", "xhigh", "max"):
            result = self.translate(top_k=0, output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}, "future": "ignored"},
                                    tools=[{"name": "read", "input_schema": {}, "strict": False}])
            self.assertEqual(result["top_k"], 0)
            self.assertEqual(result["reasoning_effort"], effort)
            self.assertEqual(result["response_format"], {"type": "json_schema", "json_schema": {"name": "anthropic_response", "schema": schema, "strict": True}})
            self.assertIs(result["tools"][0]["function"]["strict"], False)
            with mock.patch("config.get_forced_reasoning_models", return_value=["kimi-k3-1"]), mock.patch("config.get_forced_temperature", return_value=0.7):
                prepared = RequestProcessor.prepare_request(result)
            self.assertEqual(prepared.payload["reasoning_effort"], "max")
            self.assertEqual(prepared.payload["temperature"], 0.7)
            self.assertEqual(prepared.payload["response_format"], result["response_format"])
        for config in (None, {}, {"effort": None, "format": None}):
            result = self.translate(top_k=None, output_config=config, tools=[{"name": "read", "input_schema": {}, "strict": None}])
            for key in ("top_k", "reasoning_effort", "response_format"):
                self.assertNotIn(key, result)
            self.assertNotIn("strict", result["tools"][0]["function"])

    def test_invalid_mapped_options_fail(self):
        cases = [{"top_k": value} for value in (True, -1, 1.5, "2")]
        cases += [{"output_config": value} for value in ([], "secret")]
        cases += [{"output_config": {"effort": value}} for value in ([], "secret", 1)]
        cases += [{"output_config": {"format": value}} for value in ([], {"type": "text"}, {"type": "json_schema"}, {"type": "json_schema", "schema": []})]
        cases += [{"tools": [{"name": "read", "input_schema": {}, "strict": 1}]}]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(AnthropicProtocolError):
                self.translate(**overrides)

    def test_error_paths_and_text_system_messages(self):
        with self.assertRaisesRegex(AnthropicProtocolError, r"^messages\[0\].content\[0\].source must be an object$"):
            self.translate([{"type": "image", "source": None}])
        result = self.translate(messages=[{"role": "system", "content": "规则"}])
        self.assertEqual(result["messages"], [{"role": "system", "content": "规则"}])
        with self.assertRaises(AnthropicProtocolError):
            self.translate(messages=[{"role": "system", "content": []}])
