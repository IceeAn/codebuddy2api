"""本地计数与上传资源的契约测试。"""

import tests  # 在生产模块导入前隔离测试数据目录。

import json
import unittest
from unittest import mock

import config

from src.tokenizer_engine import TokenizerEngine, TokenizerError, validate_files
from src.anthropic_compat import translate_anthropic_count_request


def tokenizer_files(template=None):
    vocab = {
        word: i
        for i, word in enumerate(
            [
                "[UNK]",
                "hello",
                "world",
                "你好",
                "user",
                "assistant",
                "system",
            ]
        )
    }
    files = {
        "tokenizer.json": json.dumps(
            {
                "version": "1.0",
                "truncation": None,
                "padding": None,
                "added_tokens": [],
                "normalizer": None,
                "pre_tokenizer": {"type": "Whitespace"},
                "post_processor": None,
                "decoder": None,
                "model": {"type": "WordLevel", "vocab": vocab, "unk_token": "[UNK]"},
            }
        ).encode()
    }
    if template is not None:
        files["chat_template.jinja"] = template.encode()
    return files


class TokenizerEngineTests(unittest.TestCase):
    def test_limits_are_startup_configuration_and_strict_positive_integers(self):
        with mock.patch.dict(
            config._config_cache, {"CODEBUDDY_TOKENIZER_UPLOAD_MAX_BYTES": "12345"}
        ):
            self.assertEqual(config.get_tokenizer_limits()["upload_max_bytes"], 12345)
        for value in (0, -1, "bad", True):
            with self.subTest(value=value), mock.patch.dict(
                config._config_cache, {"CODEBUDDY_TOKENIZER_WORKERS": value}
            ):
                with self.assertRaises(ValueError):
                    config.get_tokenizer_limits()

    def test_text_uses_real_tokenizer_without_chat_overhead(self):
        engine = TokenizerEngine(tokenizer_files(), {"encoder": "auto"})
        self.assertEqual(engine.count_text("hello world 你好"), 3)
        self.assertEqual(engine.count_text(""), 0)

    def test_missing_template_uses_deterministic_budget(self):
        engine = TokenizerEngine(tokenizer_files(), {"encoder": "auto"})
        payload = {"messages": [{"role": "user", "content": "hello world"}]}
        result = engine.count_messages(payload)
        expected = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.assertEqual(result, (engine.count_text(expected), "budget_v1"))

    def test_template_counts_roles_and_generation_prompt(self):
        engine = TokenizerEngine(
            tokenizer_files(
                "{% for m in messages %}{{m.role}} {{m.content}} {% endfor %}"
                "{% if add_generation_prompt %}assistant{% endif %}"
            ),
            {"encoder": "auto"},
        )
        self.assertEqual(
            engine.count_messages(
                {"messages": [{"role": "user", "content": "hello world"}]}
            ),
            (4, "template"),
        )

    def test_invalid_template_does_not_fall_back(self):
        with self.assertRaises(TokenizerError):
            validate_files(tokenizer_files("{% invalid %}"))

    def test_python_upload_rejected(self):
        files = tokenizer_files()
        files["tokenization_custom.py"] = b"raise SystemExit()"
        with self.assertRaises(TokenizerError):
            validate_files(files)

    def test_template_without_tool_support_rejects_tools(self):
        engine = TokenizerEngine(
            tokenizer_files("{{messages[0].content}}"), {"encoder": "auto"}
        )
        with self.assertRaises(TokenizerError):
            engine.count_messages(
                {
                    "messages": [{"role": "user", "content": "hello"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {"name": "f", "parameters": {}},
                        }
                    ],
                }
            )

    def test_count_translation_does_not_require_generation_budget(self):
        result = translate_anthropic_count_request(
            {
                "model": "anthropic/codebuddy/glm-5.2",
                "messages": [{"role": "user", "content": "hello"}],
                "thinking": {"type": "enabled", "budget_tokens": 1024},
            }
        )
        self.assertEqual(result["model"], "glm-5.2")
        self.assertNotIn("max_tokens", result)

    def test_media_is_rejected_even_without_template(self):
        engine = TokenizerEngine(tokenizer_files(), {"encoder": "auto"})
        with self.assertRaises(TokenizerError):
            engine.count_messages(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": "https://example.test/image"},
                                }
                            ],
                        }
                    ]
                }
            )

    def test_hf_sidecar_added_tokens_are_applied_with_verified_ids(self):
        files = tokenizer_files()
        files["added_tokens.json"] = b'{"<extra>": 7}'
        engine = TokenizerEngine(files, {"encoder": "auto"})
        self.assertEqual(engine.count_text("<extra>"), 1)
        files["added_tokens.json"] = b'{"<extra>": 99}'
        with self.assertRaises(TokenizerError):
            TokenizerEngine(files, {"encoder": "auto"})
