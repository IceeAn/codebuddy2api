import unittest

from src.openai_compat import (
    CodeBuddyResponseEvent,
    CompletionResponseContext,
    OpenAIStreamNormalizer,
    ToolCallIndexState,
    add_openai_tool_call_indexes,
    normalize_openai_stream_chunk_envelope,
)


class NormalizeOpenAIStreamChunkEnvelopeTests(unittest.TestCase):
    def test_normalize_envelope_uses_stable_client_response_context(self):
        chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}}]}
        context = CompletionResponseContext("chatcmpl-1", 123, "codebuddy/glm-5.2")

        converted = normalize_openai_stream_chunk_envelope(chunk, context)

        self.assertEqual(converted["id"], "chatcmpl-1")
        self.assertEqual(converted["object"], "chat.completion.chunk")
        self.assertEqual(converted["created"], 123)
        self.assertEqual(converted["model"], "codebuddy/glm-5.2")
        self.assertNotIn("id", chunk)

    def test_normalize_envelope_applies_to_usage_only_chunks(self):
        context = CompletionResponseContext("id", 1, "model")
        converted = normalize_openai_stream_chunk_envelope({"usage": {"total_tokens": 1}}, context)

        self.assertEqual(converted["id"], "id")
        self.assertEqual(converted["model"], "model")


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

    def test_normalize_returns_usage_event_without_choices(self):
        chunk = {"choices": [], "usage": {"total_tokens": 1}}

        self.assertEqual(OpenAIStreamNormalizer().normalize(chunk), [chunk])

    def test_normalize_does_not_inject_role_for_unrecognized_delta(self):
        chunk = {"choices": [{"delta": {"custom": "value"}, "finish_reason": None}]}

        normalized = OpenAIStreamNormalizer().normalize(chunk)

        self.assertEqual(normalized[0]["choices"][0]["delta"], {"custom": "value"})

    def test_normalize_preserves_unrecognized_delta_shape_without_failing(self):
        chunk = {"choices": [{"delta": "unexpected", "finish_reason": None}]}

        self.assertEqual(OpenAIStreamNormalizer().normalize(chunk), [chunk])

    def test_normalize_preserves_unrecognized_function_call_shape(self):
        chunk = {
            "choices": [{
                "delta": {"content": "answer", "function_call": "unexpected"},
                "finish_reason": "stop",
            }],
        }

        normalized = OpenAIStreamNormalizer().normalize(chunk)

        self.assertEqual(normalized[-1]["choices"][0]["delta"]["function_call"], "unexpected")

    def test_normalize_preserves_unrecognized_mixed_field_values_without_failing(self):
        cases = [
            {"reasoning_content": 1, "content": {"unexpected": True}},
            {"reasoning_content": 1, "tool_calls": 1},
        ]

        for delta in cases:
            with self.subTest(delta=delta):
                chunk = {"choices": [{"delta": delta, "finish_reason": None}]}
                normalized = OpenAIStreamNormalizer().normalize(chunk)

                self.assertEqual(
                    [item["choices"][0]["delta"] for item in normalized],
                    [{"role": "assistant"}, delta],
                )

    def test_normalize_preserves_mixed_reasoning_delta(self):
        tool_calls = [{"id": "call_1", "function": {"name": "lookup"}}]
        cases = [
            ({"reasoning_content": "think", "content": "answer"}, "stop"),
            ({"reasoning_content": "think", "tool_calls": tool_calls}, "tool_calls"),
        ]

        for delta, finish_reason in cases:
            with self.subTest(delta=delta):
                chunk = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}

                normalized = OpenAIStreamNormalizer().normalize(chunk)

                self.assertEqual(
                    [item["choices"][0]["delta"] for item in normalized],
                    [{"role": "assistant"}, delta],
                )
                self.assertEqual(normalized[-1]["choices"][0]["finish_reason"], finish_reason)

    def test_remove_empty_fields_preserves_non_empty_values(self):
        delta = {
            "reasoning_content": "think",
            "content": "answer",
            "tool_calls": [{"id": "call"}],
            "function_call": {"name": "legacy"},
            "refusal": "refused",
            "extra_fields": {"key": "value"},
        }

        self.assertEqual(OpenAIStreamNormalizer._remove_empty_delta_fields(delta), delta)

class ToolCallIndexConversionTests(unittest.TestCase):
    @staticmethod
    def _convert(chunk, index_state):
        event = CodeBuddyResponseEvent.parse(chunk)
        return add_openai_tool_call_indexes(
            event,
            index_state,
        )

    def test_convert_sse_chunk_adds_stable_tool_call_indexes(self):
        index_state = ToolCallIndexState()
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

        converted_first = self._convert(first, index_state)
        converted_second = self._convert(second, index_state)

        self.assertEqual(
            [tc["index"] for tc in converted_first["choices"][0]["delta"]["tool_calls"]],
            [0, 1],
        )
        self.assertEqual(converted_second["choices"][0]["delta"]["tool_calls"][0]["index"], 0)
        self.assertNotIn("index", first["choices"][0]["delta"]["tool_calls"][0])

    def test_convert_sse_chunk_uses_current_index_for_missing_tool_id(self):
        index_state = ToolCallIndexState()
        index_state.resolve({"id": "call_1"})
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

        converted = self._convert(chunk, index_state)

        self.assertEqual(converted["choices"][0]["delta"]["tool_calls"][0]["index"], 0)

    def test_convert_sse_chunk_returns_original_when_no_tool_calls(self):
        chunk = {"choices": [{"delta": {"content": "hi"}}]}

        self.assertIs(
            self._convert(chunk, ToolCallIndexState()),
            chunk,
        )

    def test_convert_sse_chunk_returns_original_when_choices_are_missing(self):
        chunk = {"usage": {"total_tokens": 1}}

        self.assertIs(
            self._convert(chunk, ToolCallIndexState()),
            chunk,
        )

    def test_convert_sse_chunk_leaves_index_absent_without_context(self):
        chunk = {"choices": [{"delta": {"tool_calls": [{"function": {"arguments": "{}"}}]}}]}

        converted = self._convert(chunk, ToolCallIndexState())

        self.assertNotIn("index", converted["choices"][0]["delta"]["tool_calls"][0])

    def test_convert_sse_chunk_preserves_unrecognized_tool_call_items(self):
        chunk = {"choices": [{"delta": {"tool_calls": ["unexpected"]}}]}

        converted = self._convert(chunk, ToolCallIndexState())

        self.assertEqual(converted["choices"][0]["delta"]["tool_calls"], ["unexpected"])

    def test_tool_call_index_state_prefers_upstream_index(self):
        state = ToolCallIndexState()

        self.assertEqual(state.resolve({"index": 2}), 2)
        self.assertEqual(state.resolve({"id": "call_1", "index": 3}), 3)
        self.assertEqual(state.resolve({"id": "call_1"}), 3)

    def test_tool_call_index_state_allocates_an_unused_index_after_sparse_upstream_index(self):
        state = ToolCallIndexState()

        self.assertEqual(state.resolve({"id": "call_a", "index": 1}), 1)
        self.assertEqual(state.resolve({"id": "call_b"}), 0)


class CodeBuddyResponseEventTests(unittest.TestCase):
    def test_parses_shared_first_choice_semantics(self):
        chunk = {
            "usage": {"total_tokens": 2},
            "choices": [{
                "delta": {
                    "reasoning_content": "think",
                    "content": "answer",
                    "tool_calls": [{"id": "call_1"}],
                },
                "finish_reason": "tool_calls",
            }],
        }

        event = CodeBuddyResponseEvent.parse(chunk)

        self.assertIs(event.chunk_data, chunk)
        self.assertEqual(event.reasoning_content, "think")
        self.assertEqual(event.content, "answer")
        self.assertEqual(event.tool_calls, [{"id": "call_1"}])
        self.assertEqual(event.finish_reason, "tool_calls")
        self.assertEqual(event.usage, {"total_tokens": 2})

    def test_represents_chunk_without_choice_as_empty_semantic_event(self):
        chunk = {"usage": {"total_tokens": 2}}

        event = CodeBuddyResponseEvent.parse(chunk)

        self.assertFalse(event.has_choice)
        self.assertEqual(event.delta, {})
        self.assertEqual(event.usage, {"total_tokens": 2})


if __name__ == "__main__":
    unittest.main()
