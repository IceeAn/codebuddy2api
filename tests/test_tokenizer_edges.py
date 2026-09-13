"""分词格式、模板边界和官方数据加载的真实用例。"""

import tests  # 在生产模块导入前隔离测试数据目录。

import base64
import copy
import json
import unittest
from unittest import mock

import config
from src.tokenizer_engine import (
    TokenizerEngine,
    TokenizerError,
    validate_files,
    text_messages,
    _template,
)
from src.tokenizer_renderers import (
    render_segments,
    template_arguments,
    _arguments,
    _ds_calls,
    ordered_tool_results,
)
from src.tokenizer_typescript import TypeScriptSchema, encode_tools
from src.tokenizer_store import read_catalog, TokenizerStore
from tests.test_tokenizer import tokenizer_files


def kimi_files(extra=None):
    files = {
        "tiktoken.model": b"\n".join(
            base64.b64encode(bytes([i])) + b" " + str(i).encode() for i in range(256)
        )
    }
    files["tokenizer_config.json"] = json.dumps(
        {"added_tokens_decoder": extra or {"256": {"content": "<special>"}}}
    ).encode()
    return files


class TokenizerEdgesTests(unittest.TestCase):
    def test_upload_validation_and_formats(self):
        for files, profile in (
            ({}, None),
            ({"tokenizer.json": "text"}, None),
            ({**tokenizer_files(), **kimi_files()}, None),
            (tokenizer_files(), {"encoder": "python"}),
            ({"tokenizer_config.json": b"{}"}, None),
            (kimi_files(), {"format": "hf"}),
            (tokenizer_files(), {"encoder": "jinja"}),
        ):
            with self.subTest(files=list(files), profile=profile), self.assertRaises(
                TokenizerError
            ):
                validate_files(files, profile)
        self.assertEqual(validate_files(tokenizer_files())["method"], "budget_v1")
        self.assertEqual(
            validate_files(tokenizer_files("{{messages[0].content}}"))["method"],
            "template",
        )
        self.assertEqual(
            validate_files(kimi_files(), {"format": "kimi"})["format"], "kimi"
        )
        with self.assertRaises(TokenizerError) as caught:
            validate_files(
                tokenizer_files(),
                limits={**config.get_tokenizer_limits(), "upload_max_bytes": 1},
            )
        self.assertEqual(caught.exception.status_code, 413)

    def test_config_and_named_templates(self):
        for name, value in [
            ("tokenizer_config.json", b"[]"),
            ("special_tokens_map.json", b"[]"),
        ]:
            with self.assertRaises(TokenizerError):
                TokenizerEngine({**tokenizer_files(), name: value}, {})
        self.assertEqual(
            _template(
                {}, {"chat_template": [{"name": "default", "template": "hello"}]}, None
            ),
            "hello",
        )
        self.assertEqual(
            _template({}, {"chat_template": {"other": "world"}}, "other"), "world"
        )
        for templates in ({"other": "world"}, 1):
            with self.assertRaises(TokenizerError):
                _template({}, {"chat_template": templates}, None)
        files = {
            **tokenizer_files(),
            "tokenizer_config.json": b'{"added_tokens_decoder":{"7":{"content":"<x>","special":true}},"bos_token":{"content":"hello"}}',
            "added_tokens.json": b'{"<x>":7}',
        }
        self.assertEqual(TokenizerEngine(files, {}).count_text("<x>"), 1)
        files["added_tokens.json"] = b'{"<y>":7}'
        with self.assertRaises(TokenizerError):
            TokenizerEngine(files, {})

    def test_kimi_rank_and_special_validation(self):
        engine = TokenizerEngine(kimi_files(), {"format": "kimi"})
        self.assertEqual(engine.count_text("<special>"), 1)
        self.assertEqual(engine.count_text("<special>", allow_special=False), 9)
        self.assertEqual(engine.count_text("你好"), 6)
        for data in (b"YQ== 0\nYQ== 1", b"YQ== -1", b"YQ== 0", b"@@ 0"):
            with self.assertRaises(TokenizerError):
                TokenizerEngine(
                    {**kimi_files(), "tiktoken.model": data}, {"format": "kimi"}
                )
        for extra in (
            {"2": {"content": "x"}},
            {"256": {"content": "x"}, "0256": {"content": "y"}},
        ):
            with self.assertRaises(TokenizerError):
                TokenizerEngine(kimi_files(extra), {"format": "kimi"})

    def test_text_blocks_and_budget_tools(self):
        original = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello "},
                        {"type": "text", "text": "world"},
                    ],
                },
                {"role": "assistant", "content": None},
            ],
            "tools": [{"x": "你好"}],
        }
        value = copy.deepcopy(original)
        engine = TokenizerEngine(tokenizer_files(), {})
        self.assertEqual(text_messages(value)["messages"][0]["content"], "hello world")
        self.assertGreater(engine.count_messages(value)[0], 2)
        self.assertEqual(original, value)
        with self.assertRaises(TokenizerError):
            text_messages({"messages": [{"content": 42}]})
        with self.assertRaises(TokenizerError):
            engine.count_text(None)

    def test_template_sandbox_and_output_limit(self):
        body = {"messages": [{"role": "user", "content": "secret"}]}
        for template in (
            "{{raise_exception(messages[0].content)}}",
            "{{messages.__class__.__mro__}}",
        ):
            engine = TokenizerEngine(tokenizer_files(template), {})
            with self.assertRaises(TokenizerError) as caught:
                engine.count_messages(body)
            self.assertNotIn("secret", str(caught.exception))
        engine = TokenizerEngine(
            tokenizer_files("{{ messages[0].content }}"),
            {},
            {**config.get_tokenizer_limits(), "render_max_bytes": 1},
        )
        with self.assertRaises(TokenizerError) as caught:
            engine.count_messages(body)
        self.assertEqual(caught.exception.status_code, 413)
        engine = TokenizerEngine(tokenizer_files("{{tools|tojson}}"), {})
        self.assertGreater(
            engine.count_messages({**body, "tools": [{"name": "x"}]})[0], 0
        )

    def test_all_locked_official_resources_support_text_and_tools_offline(self):
        catalog = read_catalog()
        store = TokenizerStore(
            "/private/tmp/unused-tokenizer-test.sqlite",
            "/private/tmp/unused-tokenizer-test",
            catalog,
        )
        for entry in catalog["resources"]:
            with self.subTest(model=entry["model"]):
                engine = TokenizerEngine(
                    store._read_builtin(entry), store._profile(entry)
                )
                self.assertEqual(engine.count_text(""), 0)
                self.assertGreater(engine.count_text("你好，世界！"), 0)
                payload = {
                    "messages": [{"role": "user", "content": "你好"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "weather",
                                "description": "天气",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"city": {"type": "string"}},
                                    "required": ["city"],
                                },
                            },
                        }
                    ],
                }
                count, method = engine.count_messages(payload)
                self.assertGreater(count, 10)
                self.assertEqual(method, "template")

    def test_renderer_edges(self):
        messages = [
            {"role": "system", "content": "first"},
            {"role": "system", "content": "second"},
            {"role": "user", "content": "x"},
        ]
        for encoder in ("deepseek_v4", "deepseek_v41", "kimi_k3"):
            for fmt in (
                {"type": "json_object"},
                {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}},
            ):
                value = render_segments(
                    {
                        "messages": messages,
                        "response_format": fmt,
                        "tool_choice": "none",
                    },
                    encoder,
                )
                self.assertIn("response", "".join(item[0] for item in value).lower())
        value = render_segments(
            {
                "messages": [{"role": "user", "name": 'a&"b', "content": "x"}],
                "thinking": {"type": "disabled"},
                "tool_choice": "required",
            },
            "kimi_k3",
        )
        self.assertIn("a&amp;&quot;b", "".join(item[0] for item in value))
        self.assertEqual(_arguments("{}"), [])
        self.assertEqual(_arguments({"a": None}), [("a", "null", "null")])
        self.assertEqual(_arguments('{ "a" : 42 }'), [("a", "number", "42")])
        self.assertEqual(_arguments(""), [])
        self.assertIn(
            'string="false"',
            _ds_calls(
                [{"function": {"name": "x", "arguments": {"a": None}}}], "deepseek_v4"
            ),
        )
        self.assertEqual(
            ordered_tool_results(
                [{"role": "tool", "tool_call_id": "missing", "content": "x"}]
            )[0]["content"],
            "x",
        )
        payload = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}],
                }
            ],
            "reasoning_effort": "low",
            "thinking": {"clear_thinking": False},
        }
        result = template_arguments(payload, "jinja", {"bos_token": {"content": "B"}})
        self.assertEqual(
            result["messages"][0]["tool_calls"][0]["function"]["arguments"], {}
        )
        self.assertEqual(result["bos_token"], "B")
        self.assertTrue(result["preserved_thinking"])
        payload["messages"][0]["tool_calls"][0]["function"]["arguments"] = {}
        self.assertEqual(
            template_arguments(payload, "auto", {})["messages"][0]["tool_calls"][0][
                "function"
            ]["arguments"],
            {},
        )
        self.assertEqual(
            template_arguments(
                {**payload, "thinking": {"type": "disabled"}}, "hy3", {}
            )["reasoning_effort"],
            "no_think",
        )

    def test_typescript_schema_edges(self):
        registry = TypeScriptSchema()
        self.assertEqual(
            registry.render(
                {"type": "array", "items": {"type": "string", "description": "项目"}}
            ),
            "Array<\n  // 项目\n  string\n>",
        )
        for schema in ({"$ref": "#/missing"}, {"format": "unsupported"}):
            with self.assertRaises(TokenizerError):
                registry.render(schema)
        self.assertIn(
            "interface parameters",
            encode_tools(
                [
                    {
                        "function": {
                            "name": "x",
                            "parameters": {"properties": {"x": {"$ref": "#"}}},
                        }
                    }
                ]
            ),
        )
        self.assertIn(
            "interface D",
            encode_tools(
                [
                    {
                        "function": {
                            "name": "x",
                            "parameters": {"$defs": {"D": {"type": "object"}}},
                        }
                    }
                ]
            ),
        )
        self.assertEqual(encode_tools([]), "")
        self.assertEqual(registry.render({}), "any")

    def test_native_renderer_obeys_output_limit_and_hy3_preserves_low_effort(self):
        for encoder in ("deepseek_v4", "kimi_k3"):
            engine = TokenizerEngine(
                tokenizer_files(),
                {"encoder": encoder},
                {**config.get_tokenizer_limits(), "render_max_bytes": 1},
            )
            with self.assertRaises(TokenizerError) as caught:
                engine.count_messages(
                    {"messages": [{"role": "user", "content": "hello"}]}
                )
            self.assertEqual(caught.exception.status_code, 413)
        payload = {
            "messages": [{"role": "user", "content": "hello"}],
            "reasoning_effort": "low",
        }
        self.assertEqual(
            template_arguments(payload, "hy3", {})["reasoning_effort"], "low"
        )
