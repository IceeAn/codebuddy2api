import copy
import json
import unittest

from src.anthropic_compat import (
    AnthropicProtocolError,
    anthropic_thinking_signature,
    translate_anthropic_request,
)


class AnthropicRequestTranslationTests(unittest.TestCase):
    def _translate(self, **overrides):
        body = {
            "model": "anthropic/codebuddy/glm-5.2",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hello"}],
        }
        body.update(overrides)
        original = copy.deepcopy(body)
        translated = translate_anthropic_request(body)
        self.assertEqual(body, original)
        return translated

    def test_minimal_text_and_model_id_passthrough(self):
        translated = self._translate(stream=True, stop_sequences=["END"])

        self.assertEqual(translated["model"], "anthropic/codebuddy/glm-5.2")
        self.assertEqual(translated["max_tokens"], 1024)
        self.assertEqual(translated["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(translated["stop"], ["END"])
        self.assertTrue(translated["stream"])
        self.assertNotIn("stop_sequences", translated)

    def test_system_blocks_and_ordered_text_parts(self):
        translated = self._translate(
            system=[
                {"type": "text", "text": "first", "cache_control": {"type": "ephemeral"}},
                {"type": "input_text", "text": "second"},
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "a"},
                        {"type": "input_text", "text": "b"},
                    ],
                }
            ],
        )

        self.assertEqual(translated["messages"], [
            {"role": "system", "content": [{"type": "text", "text": "first"}]},
            {"role": "system", "content": [{"type": "text", "text": "second"}]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "text", "text": "b"},
                ],
            },
        ])

    def test_message_level_system_role_is_accepted_for_claude_code_compatibility(self):
        translated = self._translate(messages=[
            {"role": "user", "content": "hello"},
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "late system"},
                    {"type": "input_text", "text": "policy"},
                ],
            },
        ])

        self.assertEqual(translated["messages"], [
            {"role": "user", "content": "hello"},
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "late system"},
                    {"type": "text", "text": "policy"},
                ],
            },
        ])

    def test_tools_choice_examples_and_parallel_setting(self):
        translated = self._translate(
            tools=[{
                "name": "weather",
                "description": "Weather",
                "input_schema": {"type": "object"},
                "input_examples": [{"city": "Taipei"}, {"city": "Tokyo"}],
                "cache_control": {"type": "ephemeral"},
                "type": "custom",
            }],
            tool_choice={
                "type": "tool",
                "name": "weather",
                "disable_parallel_tool_use": True,
            },
        )

        function = translated["tools"][0]["function"]
        self.assertEqual(function["name"], "weather")
        self.assertEqual(function["parameters"], {"type": "object"})
        self.assertEqual(
            function["description"],
            'Weather\n\nInput examples:\n{"city":"Taipei"}\n{"city":"Tokyo"}',
        )
        self.assertEqual(translated["tool_choice"], {
            "type": "function",
            "function": {"name": "weather"},
        })
        self.assertFalse(translated["parallel_tool_calls"])

    def test_tool_use_and_tool_result_expansion(self):
        translated = self._translate(messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "checking"},
                    {"type": "tool_use", "id": "tool-1", "name": "weather", "input": {"city": "Taipei"}},
                    {"type": "tool_use", "id": "tool-2", "name": "clock", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": "rain", "is_error": True},
                    {"type": "tool_result", "tool_use_id": "tool-2", "content": [{"type": "text", "text": "10:00"}]},
                    {"type": "text", "text": "continue"},
                ],
            },
        ])

        self.assertEqual(translated["messages"], [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "checking"}],
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":"Taipei"}'},
                    },
                    {
                        "id": "tool-2",
                        "type": "function",
                        "function": {"name": "clock", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "tool-1", "content": "[tool_error]\nrain"},
            {"role": "tool", "tool_call_id": "tool-2", "content": [{"type": "text", "text": "10:00"}]},
            {"role": "user", "content": [{"type": "text", "text": "continue"}]},
        ])

    def test_thinking_modes_and_signed_round_trip(self):
        adaptive = self._translate(thinking={"type": "adaptive"})
        self.assertEqual(adaptive["thinking"], {"type": "enabled"})
        self.assertTrue(adaptive["enable_thinking"])

        enabled = self._translate(thinking={"type": "enabled", "budget_tokens": 2048})
        self.assertEqual(enabled["thinking"], {"type": "enabled", "budget_tokens": 2048})
        self.assertTrue(enabled["enable_thinking"])

        disabled = self._translate(thinking={"type": "disabled"})
        self.assertEqual(disabled["thinking"], {"type": "disabled"})
        self.assertFalse(disabled["enable_thinking"])

        thinking = "private chain"
        signed = self._translate(messages=[{
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": thinking,
                    "signature": anthropic_thinking_signature(thinking),
                },
                {"type": "text", "text": "answer"},
            ],
        }])
        self.assertEqual(signed["messages"][0]["reasoning_content"], thinking)

    def test_model_id_is_preserved_without_discovery_or_validation(self):
        for model in (
            "vendor/model",
            "anthropic/codebuddy/vendor/model",
            "anthropic/codebuddy/not-discovered",
        ):
            with self.subTest(model=model):
                self.assertEqual(self._translate(model=model)["model"], model)

    def test_invalid_top_level_and_scalar_types_fail(self):
        invalid_bodies = [
            {"max_tokens": True},
            {"max_tokens": -1},
            {"messages": []},
            {"stream": 1},
            {"metadata": []},
            {"stop_sequences": [1]},
            {"temperature": True},
            {"top_p": "1"},
        ]
        for overrides in invalid_bodies:
            with self.subTest(overrides=overrides), self.assertRaises(AnthropicProtocolError):
                self._translate(**overrides)

    def test_invalid_blocks_tools_results_and_signatures_fail(self):
        cases = [
            {"system": [{"type": "image", "source": {}}]},
            {"messages": [{"role": "developer", "content": "x"}]},
            {"messages": [{"role": "user", "content": []}]},
            {"messages": [{"role": "user", "content": [{"type": "image", "source": {}}]}]},
            {"tools": [{"name": "x", "description": 1, "input_schema": {}}]},
            {"tools": [{"name": "x", "input_schema": {}, "input_examples": [1]}]},
            {"tools": [{"name": "x", "input_schema": {}}], "tool_choice": {"type": "tool", "name": "missing"}},
            {"thinking": {"type": "enabled", "budget_tokens": True}},
            {"messages": [{"role": "assistant", "content": [{"type": "redacted_thinking", "data": "x"}]}]},
            {"messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "x", "signature": "native"}]}]},
            {"messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "x", "input": {}}]},
                {"role": "user", "content": [{"type": "text", "text": "before"}, {"type": "tool_result", "tool_use_id": "a", "content": "x"}]},
            ]},
            {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "unknown", "content": "x"}]}]},
        ]
        for overrides in cases:
            with self.subTest(overrides=json.dumps(overrides, ensure_ascii=False)), self.assertRaises(AnthropicProtocolError):
                self._translate(**overrides)

    def test_remaining_strict_validation_and_success_variants(self):
        tool = {"name": "x", "input_schema": {}}
        invalid_overrides = [
            {"model": ""},
            {"system": []},
            {"system": [1]},
            {"system": [{"type": "image", "text": "x"}]},
            {"messages": [{"role": "assistant", "content": []}]},
            {"messages": [{"role": "assistant", "content": [1]}]},
            {"messages": [{"role": "assistant", "content": [{"type": "unknown"}]}]},
            {"messages": [{"role": "user", "content": [1]}]},
            {"messages": [1]},
            {"messages": [{"role": "user"}]},
            {"tools": {}},
            {"tools": [1]},
            {"tools": [{"name": "x", "type": "computer_20250124", "input_schema": {}}]},
            {"tools": [{"name": "x", "input_schema": []}]},
            {"tools": [tool, tool]},
            {"tool_choice": "auto"},
            {"tools": [tool], "tool_choice": {"type": "auto", "name": "x"}},
            {"tools": [tool], "tool_choice": {"type": "any", "name": "x"}},
            {"tools": [tool], "tool_choice": {"type": "none", "name": "x"}},
            {"tools": [tool], "tool_choice": {"type": "mystery"}},
            {"tools": [tool], "tool_choice": {"type": "auto", "disable_parallel_tool_use": 1}},
            {"thinking": "adaptive"},
            {"thinking": {"type": "adaptive", "budget_tokens": 1}},
            {"thinking": {"type": "disabled", "budget_tokens": 1}},
            {"thinking": {"type": "mystery"}},
            {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "x", "input": []}]}]},
            {"messages": [{"role": "assistant", "content": [
                {"type": "tool_use", "id": "x", "name": "x", "input": {}},
                {"type": "tool_use", "id": "x", "name": "x", "input": {}},
            ]}]},
            {"messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "x", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x"}]},
            ]},
            {"messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "x", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": [], "is_error": False}]},
            ]},
            {"messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "x", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok", "is_error": 1}]},
            ]},
        ]
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides), self.assertRaises(AnthropicProtocolError):
                self._translate(**overrides)

        invalid_bodies = [None, [], {}, {"model": "glm"}, {"model": "glm", "max_tokens": 1}]
        for body in invalid_bodies:
            with self.subTest(body=body), self.assertRaises(AnthropicProtocolError):
                translate_anthropic_request(body)

        for choice_type, expected in (("auto", "auto"), ("any", "required"), ("none", "none")):
            translated = self._translate(tools=[tool], tool_choice={"type": choice_type})
            self.assertEqual(translated["tool_choice"], expected)
            self.assertNotIn("parallel_tool_calls", translated)

        no_description = self._translate(tools=[tool])
        self.assertNotIn("description", no_description["tools"][0]["function"])
        empty_examples = self._translate(tools=[{**tool, "input_examples": []}])
        self.assertEqual(empty_examples["tools"][0]["function"]["description"], "Input examples:")
        assistant_string = self._translate(messages=[{"role": "assistant", "content": "answer"}])
        self.assertEqual(assistant_string["messages"], [{"role": "assistant", "content": "answer"}])
        numeric = self._translate(temperature=0.5, top_p=1)
        self.assertEqual((numeric["temperature"], numeric["top_p"]), (0.5, 1))

        empty_optional_arrays = self._translate(tools=[], stop_sequences=[])
        self.assertNotIn("tools", empty_optional_arrays)
        self.assertNotIn("stop", empty_optional_arrays)

        errored_parts = self._translate(messages=[
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "x", "input": {}}]},
            {"role": "user", "content": [{
                "type": "tool_result",
                "tool_use_id": "x",
                "content": [{"type": "text", "text": "bad"}],
                "is_error": True,
            }]},
        ])
        self.assertEqual(errored_parts["messages"][1]["content"][0]["text"], "[tool_error]\nbad")

    def test_unsupported_fields_are_accepted_and_ignored_without_mutation(self):
        translated = self._translate(
            output_config={"format": {"type": "json_schema"}},
            top_k=20,
            service_tier="auto",
            context_management={"edits": []},
            mcp_servers=[{"name": "ignored"}],
            future_top_level={"enabled": True},
            messages=[{
                "role": "user",
                "content": [{"type": "text", "text": "hello", "citations": []}],
                "future_message_field": True,
            }],
            tools=[{
                "name": "weather",
                "input_schema": {"type": "object"},
                "strict": True,
                "defer_loading": True,
            }],
            tool_choice={"type": "auto", "future_choice_field": "ignored"},
            thinking={"type": "adaptive", "future_thinking_field": "ignored"},
        )

        self.assertEqual(translated["messages"], [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ])
        self.assertEqual(translated["tools"][0]["function"]["name"], "weather")
        for field in (
            "output_config", "top_k", "service_tier", "context_management",
            "mcp_servers", "future_top_level",
        ):
            self.assertNotIn(field, translated)


if __name__ == "__main__":
    unittest.main()
