import unittest

from src.openai_compat import (
    OpenAICompatibilityConverter,
    OpenAIStreamNormalizer,
    ensure_openai_stream_chunk_fields,
    validate_and_fix_tool_call_args,
)


class EnsureOpenAIStreamChunkFieldsTests(unittest.TestCase):
    def test_ensure_fields_adds_required_openai_chunk_metadata(self):
        chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}}]}

        converted = ensure_openai_stream_chunk_fields(chunk, "chatcmpl-1", 123, "glm-5.2")

        self.assertEqual(converted["id"], "chatcmpl-1")
        self.assertEqual(converted["object"], "chat.completion.chunk")
        self.assertEqual(converted["created"], 123)
        self.assertEqual(converted["model"], "glm-5.2")
        self.assertNotIn("id", chunk)

    def test_ensure_fields_ignores_non_chunk_inputs(self):
        for value in ({}, [], "bad"):
            with self.subTest(value=value):
                self.assertIs(ensure_openai_stream_chunk_fields(value, "id", 1, "model"), value)

    def test_ensure_fields_does_not_add_empty_model(self):
        converted = ensure_openai_stream_chunk_fields({"choices": []}, "id", 1, "")

        self.assertNotIn("model", converted)


class OpenAIStreamNormalizerTests(unittest.TestCase):
    def test_normalize_injects_assistant_role_once_and_removes_empty_fields(self):
        normalizer = OpenAIStreamNormalizer()
        first = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "reasoning_content": "我",
                        "content": "",
                        "tool_calls": [],
                        "function_call": None,
                        "refusal": "",
                        "extra_fields": {},
                    },
                    "finish_reason": None,
                }
            ]
        }
        second = {
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "", "content": "结论"},
                    "finish_reason": None,
                }
            ]
        }

        first_chunks = normalizer.normalize(first)
        second_chunks = normalizer.normalize(second)

        self.assertEqual([chunk["choices"][0]["delta"] for chunk in first_chunks], [
            {"role": "assistant"},
            {"reasoning_content": "我"},
        ])
        self.assertEqual([chunk["choices"][0]["delta"] for chunk in second_chunks], [{"content": "结论"}])

    def test_normalize_keeps_empty_delta_when_finish_reason_or_usage_exists(self):
        normalizer = OpenAIStreamNormalizer()
        chunk = {
            "usage": {"prompt_tokens": 1},
            "choices": [{"index": 0, "delta": {"role": ""}, "finish_reason": "stop"}],
        }

        normalized = normalizer.normalize(chunk)

        self.assertEqual(normalized[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(normalized[1]["choices"][0]["delta"], {})
        self.assertEqual(normalized[1]["choices"][0]["finish_reason"], "stop")

    def test_normalize_drops_empty_delta_without_finish_reason_or_usage(self):
        normalizer = OpenAIStreamNormalizer()
        chunk = {"choices": [{"index": 0, "delta": {"refusal": ""}, "finish_reason": None}]}

        self.assertEqual(normalizer.normalize(chunk), [])

    def test_normalize_returns_unmodified_for_non_standard_shapes(self):
        normalizer = OpenAIStreamNormalizer()
        chunks = [
            {"choices": []},
            {"choices": [{"delta": "bad"}]},
            {"object": "not-a-chat-chunk"},
        ]

        for chunk in chunks:
            with self.subTest(chunk=chunk):
                self.assertEqual(normalizer.normalize(chunk), [chunk])


class OpenAICompatibilityConverterTests(unittest.TestCase):
    def test_convert_sse_chunk_adds_stable_tool_call_indexes(self):
        index_map = {}
        first = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                            {"id": "call_2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                        ]
                    }
                }
            ]
        }
        second = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"arguments": "more"}},
                        ]
                    }
                }
            ]
        }

        converted_first = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(first, index_map)
        converted_second = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(second, index_map)

        self.assertEqual(
            [tc["index"] for tc in converted_first["choices"][0]["delta"]["tool_calls"]],
            [0, 1],
        )
        self.assertEqual(converted_second["choices"][0]["delta"]["tool_calls"][0]["index"], 0)
        self.assertNotIn("index", first["choices"][0]["delta"]["tool_calls"][0])

    def test_convert_sse_chunk_uses_current_index_for_missing_tool_id(self):
        index_map = {"call_1": 0}
        chunk = {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"type": "function", "function": {"arguments": "continued"}},
                        ]
                    }
                }
            ]
        }

        converted = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(chunk, index_map)

        self.assertEqual(converted["choices"][0]["delta"]["tool_calls"][0]["index"], 0)

    def test_convert_sse_chunk_returns_original_when_no_tool_calls(self):
        chunk = {"choices": [{"delta": {"content": "hi"}}]}

        self.assertIs(OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(chunk, {}), chunk)


class ToolCallArgsTests(unittest.TestCase):
    def test_validate_and_fix_tool_call_args_partitions_common_inputs(self):
        cases = [
            ("", "{}"),
            ('{"query":"hi"}', '{"query":"hi"}'),
            ('{"a":1}{"b":2}', '{"a": 1}'),
            ('{"a":1', '{"a":1}'),
            ('[1,2', "[1,2]"),
            ("not-json", "{}"),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(validate_and_fix_tool_call_args(value), expected)


if __name__ == "__main__":
    unittest.main()
