import unittest

from src.keyword_replacer import (
    apply_keyword_replacement,
    apply_keyword_replacement_to_system_message,
)


class KeywordReplacerTests(unittest.TestCase):
    def test_replaces_all_supported_competitor_references(self):
        text = (
            "Claude Code by Anthropic. "
            "Anthropic's official CLI for Claude: "
            "https://github.com/anthropics/claude-code/issues"
        )

        result = apply_keyword_replacement(text)

        self.assertNotIn("Claude", result)
        self.assertNotIn("Anthropic", result)
        self.assertIn("CodeBuddy", result)
        self.assertIn("Tencent", result)

    def test_non_string_text_and_non_supported_content_are_unchanged(self):
        marker = {"value": 1}

        self.assertIs(apply_keyword_replacement(marker), marker)
        self.assertIs(apply_keyword_replacement_to_system_message(marker), marker)

    def test_list_content_only_replaces_text_items(self):
        content = [
            {"type": "text", "text": "Claude"},
            {"type": "image", "text": "Claude"},
            "plain item",
        ]

        result = apply_keyword_replacement_to_system_message(content)

        self.assertEqual(result[0]["text"], "CodeBuddy")
        self.assertEqual(result[1]["text"], "Claude")


if __name__ == "__main__":
    unittest.main()
