import unittest

from src.system_prompt_rewriter import (
    rewrite_system_prompt_content,
    rewrite_system_prompt_messages,
)


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


class SystemPromptRewriterTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
