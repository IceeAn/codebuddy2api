
import tests  # 在生产模块导入前隔离测试数据目录。
import copy
import json
import unittest
from unittest import mock

from src.system_prompt_rewriter import (
    rewrite_system_prompt_content,
    rewrite_system_prompt_messages,
)
from src.anthropic_compat import translate_anthropic_request
from src.request_processor import RequestProcessor
from src.stream_service import CodeBuddyStreamService
from tests.helpers import ConfigIsolationMixin, FakeHttpClient


IDENTITY_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."
NEUTRAL_IDENTITY_PROMPT = (
    "You are an interactive software engineering assistant operating in a "
    "command-line environment."
)
MAIN_BRANCH_PREFIX = "Main branch (you will usually use this for PRs):"
ATTRIBUTION_HEADER = (
    "x-anthropic-billing-header: cc_version=2.1.246.0c3; "
    "cc_entrypoint=sdk-cli;"
)
DSH_IDENTITY = "You are an AI agent powered by DeepSeek Harness."
DSH_PATHS = (
    "Tokens prefixed with @ are paths the user explicitly referenced. "
    "Relative paths resolve from the workspace root; absolute paths identify files or directories on the host."
)
DSH_GUI = (
    "You are interacting with the user through the DeepSeek Harness Web GUI whose URL the runtime context names. "
    'When the user refers to "this page", "this GUI", or "this app" without naming another target, they mean this GUI.'
)
DSH_PROMPT = "\n\n".join((DSH_IDENTITY, DSH_PATHS, DSH_GUI))
REWRITTEN_DSH_PROMPT = (
    "You are an AI agent, powered by DeepSeek Harness.\n\n"
    "Tokens prefixed with @ are paths the user explicitly referenced.\n"
    "Relative paths resolve from the workspace root; absolute paths identify files or directories on the host.\n\n"
    "You are interacting with the user through the DeepSeek Harness Web GUI whose URL the runtime context names.\n"
    'When the user refers to "this page", "this GUI", or "this app" without naming another target, they mean this GUI.'
)


class SystemPromptRewriterTests(unittest.TestCase):
    def test_dsh_rewrite_changes_only_one_comma_and_two_spaces(self):
        original = "保留前文\n" + DSH_PROMPT + "\n保留后文"
        expected = "保留前文\n" + REWRITTEN_DSH_PROMPT + "\n保留后文"
        result = rewrite_system_prompt_content(original)
        self.assertEqual(result, expected)
        self.assertEqual(len(result), len(original) + 1)
        self.assertEqual(rewrite_system_prompt_content(result), result)

    def test_dsh_rewrite_preserves_blocks_other_roles_and_message_order(self):
        messages = [
            {"role": "system", "content": [{"type": "text", "text": DSH_PROMPT,
                                             "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": DSH_PROMPT},
            {"role": "assistant", "content": DSH_PROMPT},
            {"role": "tool", "tool_call_id": "call_check", "content": DSH_PROMPT},
            {"role": "system", "content": "保留后续系统规则"},
        ]
        original = copy.deepcopy(messages)
        rewrite_system_prompt_messages(messages)
        self.assertEqual(messages[0]["content"], [{
            "type": "text", "text": REWRITTEN_DSH_PROMPT, "cache_control": {"type": "ephemeral"},
        }])
        self.assertEqual(messages[1:], original[1:])

    def test_dsh_rewrite_does_not_match_single_sentences_or_generic_fragments(self):
        for text in (
            "DeepSeek Harness", "referenced. Relative paths", "context names. When the user",
            "Tokens prefixed with @ are paths the user explicitly referenced.",
            "Relative paths resolve from the workspace root; absolute paths identify files or directories on the host.",
            "You are interacting with the user through the DeepSeek Harness Web GUI whose URL the runtime context names.",
            DSH_PATHS.replace("Tokens", "tokens", 1), DSH_GUI.replace("GUI", "UI", 1),
            DSH_GUI.replace('"', r'\"'),
        ):
            with self.subTest(text=text):
                self.assertEqual(rewrite_system_prompt_content(text), text)

    def test_removes_attribution_only_system_message(self):
        messages = [
            {
                "role": "system",
                "content": [{"type": "text", "text": ATTRIBUTION_HEADER}],
            },
            {"role": "system", "content": IDENTITY_PROMPT},
            {"role": "user", "content": ATTRIBUTION_HEADER},
        ]

        rewrite_system_prompt_messages(messages)

        self.assertEqual(messages, [
            {"role": "system", "content": NEUTRAL_IDENTITY_PROMPT},
            {"role": "user", "content": ATTRIBUTION_HEADER},
        ])

    def test_removes_attribution_line_but_preserves_following_system_text(self):
        messages = [
            {
                "role": "system",
                "content": f"{ATTRIBUTION_HEADER}\r\nKeep this policy.",
            },
        ]

        rewrite_system_prompt_messages(messages)

        self.assertEqual(
            messages,
            [{"role": "system", "content": "Keep this policy."}],
        )

    def test_removes_attribution_text_block_but_preserves_siblings(self):
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": ATTRIBUTION_HEADER},
                    {"type": "text", "text": "Keep this policy."},
                    {"type": "image", "text": ATTRIBUTION_HEADER},
                ],
            },
        ]

        rewrite_system_prompt_messages(messages)

        self.assertEqual(messages, [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "Keep this policy."},
                    {"type": "image", "text": ATTRIBUTION_HEADER},
                ],
            },
        ])

    def test_preserves_attribution_near_matches(self):
        messages = [
            {
                "role": "system",
                "content": "X-Anthropic-Billing-Header: keep this text",
            },
            {"role": "assistant", "content": ATTRIBUTION_HEADER},
        ]

        rewrite_system_prompt_messages(messages)

        self.assertEqual(messages, [
            {
                "role": "system",
                "content": "X-Anthropic-Billing-Header: keep this text",
            },
            {"role": "assistant", "content": ATTRIBUTION_HEADER},
        ])

    def test_handles_other_system_content_shapes_without_broadening_match(self):
        marker = object()
        messages = [
            {"role": "system", "content": marker},
            {
                "role": "system",
                "content": [{"type": "text", "text": 123}],
            },
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": f"{ATTRIBUTION_HEADER}\nKeep this policy.",
                    },
                ],
            },
            {"role": "system", "content": ATTRIBUTION_HEADER},
        ]

        rewrite_system_prompt_messages(messages)

        self.assertIs(messages[0]["content"], marker)
        self.assertEqual(messages[1]["content"], [{"type": "text", "text": 123}])
        self.assertEqual(
            messages[2]["content"],
            [{"type": "text", "text": "Keep this policy."}],
        )
        self.assertEqual(len(messages), 3)

    def test_rewrites_only_exact_fingerprints_in_string_content(self):
        content = (
            f"{IDENTITY_PROMPT}\n"
            f"{MAIN_BRANCH_PREFIX} feature/test\n"
            "Claude and Anthropic remain."
        )

        result = rewrite_system_prompt_content(content)

        self.assertEqual(
            result,
            f"{NEUTRAL_IDENTITY_PROMPT}\n"
            "Main branch: feature/test\n"
            "Claude and Anthropic remain.",
        )

    def test_rewrites_text_blocks_without_touching_other_items(self):
        content = [
            {
                "type": "text",
                "text": f"{IDENTITY_PROMPT}\n{MAIN_BRANCH_PREFIX} main",
            },
            {"type": "text", "text": 123},
            {"type": "image", "text": IDENTITY_PROMPT},
            "plain item",
        ]

        result = rewrite_system_prompt_content(content)

        self.assertIs(result, content)
        self.assertEqual(
            content[0]["text"],
            f"{NEUTRAL_IDENTITY_PROMPT}\nMain branch: main",
        )
        self.assertEqual(content[1]["text"], 123)
        self.assertEqual(content[2]["text"], IDENTITY_PROMPT)
        self.assertEqual(content[3], "plain item")

    def test_preserves_near_matches_and_non_text_content(self):
        near_match = (
            "You are claude code, Anthropic's official CLI for Claude.\n"
            "Main branch: main"
        )
        marker = object()

        self.assertEqual(rewrite_system_prompt_content(near_match), near_match)
        self.assertIs(rewrite_system_prompt_content(marker), marker)


class SystemPromptUpstreamTests(ConfigIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_upstream_preserves_full_system_messages_in_both_response_modes(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                body = {
                    "model": "deepseek-v4.1-flash", "max_tokens": 256000, "stream": stream,
                    "thinking": {"type": "enabled"}, "output_config": {"effort": "max"},
                    "system": [{"type": "text", "text": "保留前文\n" + DSH_PROMPT + "\n保留后文"},
                               {"type": "text", "text": "保留独立系统规则"}],
                    "tools": [{"name": "check", "input_schema": {"type": "object"}}],
                    "messages": [{"role": "user", "content": DSH_PROMPT}],
                }
                original = copy.deepcopy(body)
                prepared = RequestProcessor.prepare_request(translate_anthropic_request(body))
                self.assertEqual(prepared.payload["messages"], [
                    {"role": "system", "content": [{"type": "text", "text": "保留前文\n" + REWRITTEN_DSH_PROMPT + "\n保留后文"}]},
                    {"role": "system", "content": [{"type": "text", "text": "保留独立系统规则"}]},
                    {"role": "user", "content": DSH_PROMPT},
                ])
                client = FakeHttpClient([
                    "data: " + json.dumps({"choices": [{"delta": {"content": "完成"}, "finish_reason": "stop"}]}) + "\n\n",
                    "data: [DONE]\n\n",
                ])
                service = CodeBuddyStreamService(http_client_factory=mock.AsyncMock(return_value=client))
                if stream:
                    response = await service.handle_stream_response(prepared.payload, {})
                    self.assertTrue([chunk async for chunk in response.body_iterator])
                else:
                    response = await service.handle_non_stream_response(prepared.payload, {})
                    self.assertEqual(response["choices"][0]["message"]["content"], "完成")
                self.assertEqual(client.requests[0]["json"], prepared.payload)
                self.assertEqual(body, original)


if __name__ == "__main__":
    unittest.main()
