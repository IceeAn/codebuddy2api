"""与固定官方编码器生成的参考结果逐段比较。"""

import tests  # 在生产模块导入前隔离测试数据目录。

import json
from pathlib import Path
import unittest

from src.tokenizer_renderers import render_segments


class TokenizerRendererTests(unittest.TestCase):
    def test_kimi_typescript_tool_declarations(self):
        from src.tokenizer_typescript import encode_tools

        cases = json.loads(
            (Path(__file__).parent / "fixtures/tokenizer_tools.json").read_text()
        )
        for case in cases:
            self.assertEqual(encode_tools(case["tools"]), case["expected"])

    def test_official_reference_conversations(self):
        cases = json.loads(
            (Path(__file__).parent / "fixtures/tokenizer_rendering.json").read_text()
        )
        for case in cases:
            with self.subTest(encoder=case["encoder"], payload=case["payload"]):
                self.assertEqual(
                    [
                        list(part)
                        for part in render_segments(case["payload"], case["encoder"])
                    ],
                    case["expected"],
                )


class OfficialTemplateThinkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from src.tokenizer_engine import TokenizerEngine
        from src.tokenizer_store import get_tokenizer_store

        store = get_tokenizer_store()
        cls.engines = {}
        for model in ("minimax-m3", "kimi-k2.6", "hy4-preview"):
            entry = store._builtin(model)
            cls.engines[model] = TokenizerEngine(
                store._read_builtin(entry), store._profile(entry)
            )

    def test_minimax_three_modes_use_official_instructions_and_generation_prefix(self):
        engine = self.engines["minimax-m3"]
        counts = {}
        for mode, suffix in (
            ("disabled", "</mm:think>"),
            ("adaptive", ""),
            ("enabled", "<mm:think>"),
        ):
            with self.subTest(mode=mode):
                payload = {
                    "messages": [{"role": "user", "content": "你好"}],
                    "thinking": {"type": mode},
                }
                expected = engine.template.render(
                    messages=payload["messages"],
                    tools=[],
                    add_generation_prompt=True,
                    thinking_mode=mode,
                )
                self.assertIn("Current thinking mode: " + mode, expected)
                if suffix:
                    self.assertTrue(expected.endswith(suffix), repr(expected[-80:]))
                counts[mode] = engine.count_text(expected)
                self.assertEqual(
                    engine.count_messages(payload), (counts[mode], "template")
                )
        self.assertEqual(len(set(counts.values())), 3)

    def test_thinking_flags_default_and_explicit_disable_match_request_policy(self):
        from src.tokenizer_renderers import template_arguments

        messages = [{"role": "user", "content": "你好"}]
        self.assertEqual(
            template_arguments({"messages": messages}, "jinja", {})["thinking_mode"],
            "enabled",
        )
        arguments = template_arguments(
            {
                "messages": messages,
                "thinking": {"type": "adaptive"},
                "enable_thinking": False,
            },
            "jinja",
            {},
        )
        self.assertEqual(arguments["thinking_mode"], "disabled")

    def test_kimi_history_preservation_and_hy4_spelling_remain_distinct(self):
        messages = [
            {"role": "user", "content": "第一问"},
            {
                "role": "assistant",
                "reasoning_content": "历史推理测试内容",
                "content": "第一答",
            },
            {"role": "user", "content": "第二问"},
        ]
        for model, variable in (
            ("kimi-k2.6", "preserve_thinking"),
            ("hy4-preview", "preserved_thinking"),
        ):
            engine = self.engines[model]
            counts = {}
            for clear in (True, False):
                with self.subTest(model=model, clear=clear):
                    expected = engine.template.render(
                        messages=messages,
                        tools=[],
                        add_generation_prompt=True,
                        thinking=True,
                        reasoning_effort="high",
                        **{variable: not clear}
                    )
                    self.assertEqual("历史推理测试内容" in expected, not clear)
                    counts[clear] = engine.count_text(expected)
                    self.assertEqual(
                        engine.count_messages(
                            {
                                "messages": messages,
                                "thinking": {
                                    "type": "enabled",
                                    "clear_thinking": clear,
                                },
                            }
                        ),
                        (counts[clear], "template"),
                    )
            self.assertGreater(counts[False], counts[True])

    def test_anthropic_modes_count_the_effective_upstream_payload(self):
        from src.anthropic_compat import translate_anthropic_count_request
        from src.request_processor import apply_request_policies
        from unittest import mock

        engine = self.engines["minimax-m3"]
        for incoming, effective in (
            ("disabled", "disabled"),
            ("adaptive", "enabled"),
            ("enabled", "enabled"),
        ):
            thinking = {"type": incoming}
            if incoming == "enabled":
                thinking["budget_tokens"] = 1024
            payload = translate_anthropic_count_request(
                {
                    "model": "minimax-m3",
                    "messages": [{"role": "user", "content": "你好"}],
                    "thinking": thinking,
                }
            )
            with mock.patch("config.get_forced_reasoning_models", return_value=[]):
                apply_request_policies(payload)
            expected = engine.template.render(
                messages=payload["messages"],
                tools=[],
                add_generation_prompt=True,
                thinking_mode=effective,
            )
            self.assertEqual(
                engine.count_messages(payload),
                (engine.count_text(expected), "template"),
            )
