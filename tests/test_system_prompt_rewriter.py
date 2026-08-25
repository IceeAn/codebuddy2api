import unittest

from src.system_prompt_rewriter import rewrite_system_prompt_content


IDENTITY_PROMPT = "You are Claude Code, Anthropic's official CLI for Claude."
NEUTRAL_IDENTITY_PROMPT = (
    "You are an interactive software engineering assistant operating in a "
    "command-line environment."
)
MAIN_BRANCH_PREFIX = "Main branch (you will usually use this for PRs):"


class SystemPromptRewriterTests(unittest.TestCase):
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
