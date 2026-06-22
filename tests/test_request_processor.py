import json
import unittest

import config
from fastapi import HTTPException

from src.request_processor import (
    RequestProcessor,
    is_false_like,
    normalize_model_id,
    strip_model_namespace,
    should_configure_model_reasoning,
)

from tests.helpers import ConfigIsolationMixin


class RequestProcessorPreparePayloadTests(ConfigIsolationMixin, unittest.TestCase):
    def test_prepare_payload_uses_first_configured_model_when_missing(self):
        config._config_cache["CODEBUDDY_MODELS"] = ",".join(config.DEFAULT_CODEBUDDY_MODELS)

        payload = RequestProcessor.prepare_payload({
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["model"], "glm-5.2")

    def test_prepare_payload_forces_deepseek_v4_reasoning(self):
        payload = RequestProcessor.prepare_payload({
            "model": "deepseek-v4-pro",
            "reasoning_effort": "max",
            "thinking": {"type": "enabled"},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_prepare_payload_forces_namespaced_deepseek_v4_reasoning(self):
        payload = RequestProcessor.prepare_payload({
            "model": "codebuddy/deepseek-v4-flash",
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled"})

    def test_prepare_payload_forces_glm_5_1_reasoning(self):
        payload = RequestProcessor.prepare_payload({
            "model": "glm-5.1",
            "reasoning_effort": "low",
            "thinking": {"type": "disabled", "clear_thinking": True},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled", "clear_thinking": True})
        self.assertNotIn("enable_thinking", payload)

    def test_prepare_payload_forces_glm_5_2_reasoning(self):
        payload = RequestProcessor.prepare_payload({
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertNotIn("enable_thinking", payload)

    def test_prepare_payload_does_not_force_reasoning_for_other_models(self):
        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "reasoning_effort": "low",
            "thinking": {"type": "disabled"},
            "temperature": 0.2,
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", payload)
        self.assertEqual(payload["temperature"], 1)

    def test_prepare_payload_enables_thinking_for_other_models_by_default(self):
        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertIs(payload["enable_thinking"], True)

    def test_prepare_payload_strips_model_namespace_when_enabled(self):
        config._config_cache["CODEBUDDY_STRIP_MODEL_NAMESPACE"] = True

        payload = RequestProcessor.prepare_payload({
            "model": "codebuddy/lite",
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["model"], "lite")

    def test_prepare_payload_preserves_model_namespace_when_disabled(self):
        false_values = [False, ""]

        for value in false_values:
            with self.subTest(value=value):
                config._config_cache["CODEBUDDY_STRIP_MODEL_NAMESPACE"] = value

                payload = RequestProcessor.prepare_payload({
                    "model": "codebuddy/lite",
                    "messages": [{"role": "user", "content": "test"}],
                })

                self.assertEqual(payload["model"], "codebuddy/lite")

    def test_prepare_payload_preserves_explicit_enable_thinking_false(self):
        false_like_values = [False, 0, "false", "0", "no", "off", "disabled"]

        for value in false_like_values:
            with self.subTest(value=value):
                payload = RequestProcessor.prepare_payload({
                    "model": "lite",
                    "enable_thinking": value,
                    "messages": [{"role": "user", "content": "test"}],
                })
                self.assertEqual(payload["enable_thinking"], value)

    def test_prepare_payload_respects_disabled_thinking_type_for_other_models(self):
        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", payload)

    def test_prepare_payload_uses_configured_forced_reasoning_models(self):
        config._config_cache["CODEBUDDY_FORCED_REASONING_MODELS"] = "lite"

        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "reasoning_effort": "low",
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "max")
        self.assertEqual(payload["thinking"], {"type": "enabled"})

    def test_prepare_payload_skips_forced_reasoning_when_config_empty(self):
        config._config_cache["CODEBUDDY_FORCED_REASONING_MODELS"] = ""

        payload = RequestProcessor.prepare_payload({
            "model": "glm-5.2",
            "reasoning_effort": "low",
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("enable_thinking", payload)

    def test_prepare_payload_uses_configured_forced_temperature(self):
        config._config_cache["CODEBUDDY_FORCED_TEMPERATURE"] = "0.7"

        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "temperature": 0.2,
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["temperature"], 0.7)

    def test_prepare_payload_preserves_temperature_when_forcing_disabled(self):
        config._config_cache["CODEBUDDY_FORCED_TEMPERATURE"] = ""

        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "temperature": 0.2,
            "messages": [{"role": "user", "content": "test"}],
        })
        payload_without_temperature = RequestProcessor.prepare_payload({
            "model": "lite",
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["temperature"], 0.2)
        self.assertNotIn("temperature", payload_without_temperature)

    def test_prepare_payload_preserves_tool_call_ids(self):
        request_body = {
            "model": "glm-5.1",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_search_1",
                            "type": "function",
                            "function": {"name": "search", "arguments": "{}"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_search_1",
                    "content": "result",
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "toolUseId": "call_search_2",
                            "content": "structured result",
                        }
                    ],
                },
            ],
        }

        payload = RequestProcessor.prepare_payload(request_body)

        self.assertEqual(payload["messages"][0]["tool_calls"][0]["id"], "call_search_1")
        self.assertEqual(payload["messages"][1]["tool_call_id"], "call_search_1")
        self.assertEqual(payload["messages"][2]["content"][0]["toolUseId"], "call_search_2")
        self.assertEqual(request_body["messages"][0]["tool_calls"][0]["id"], "call_search_1")

    def test_prepare_payload_does_not_mutate_request_body(self):
        request_body = {
            "model": "glm-5.1",
            "messages": [
                {
                    "role": "system",
                    "content": "You are Claude.",
                },
                {
                    "role": "user",
                    "content": "test",
                },
            ],
            "stream_options": {"include_usage": False},
            "thinking": {"type": "disabled"},
        }
        original_body = json.loads(json.dumps(request_body))

        payload = RequestProcessor.prepare_payload(request_body)

        self.assertEqual(request_body, original_body)
        self.assertNotEqual(payload["messages"][0]["content"], original_body["messages"][0]["content"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_prepare_payload_preserves_stream_options_except_include_usage(self):
        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "messages": [{"role": "user", "content": "test"}],
            "stream_options": {"include_usage": False, "foo": "bar"},
        })

        self.assertEqual(payload["stream_options"], {"include_usage": True, "foo": "bar"})

    def test_prepare_payload_replaces_system_text_items_in_list_content(self):
        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {"type": "text", "text": "Claude and Anthropic"},
                        {"type": "image", "text": "Claude"},
                    ],
                },
                {"role": "user", "content": "test"},
            ],
        })

        content = payload["messages"][0]["content"]
        self.assertEqual(content[0]["text"], "CodeBuddy and Tencent")
        self.assertEqual(content[1]["text"], "Claude")

    def test_prepare_payload_adds_default_system_message_for_single_user_message(self):
        payload = RequestProcessor.prepare_payload({
            "model": "lite",
            "messages": [{"role": "user", "content": "test"}],
        })

        self.assertEqual(payload["messages"][0], {"role": "system", "content": "You are a helpful assistant."})


class RequestProcessorValidationTests(unittest.TestCase):
    def test_validate_request_accepts_assistant_tool_call_without_content(self):
        RequestProcessor.validate_request({
            "messages": [
                {"role": "user", "content": "test"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_search_1",
                            "type": "function",
                            "function": {"name": "search", "arguments": "{}"},
                        }
                    ],
                },
            ],
        })

    def test_validate_request_rejects_invalid_body_equivalence_classes(self):
        invalid_bodies = [
            None,
            [],
            "bad",
        ]

        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaises(HTTPException) as context:
                    RequestProcessor.validate_request(body)
                self.assertEqual(context.exception.status_code, 400)

    def test_validate_request_rejects_missing_or_non_list_messages(self):
        invalid_bodies = [
            {},
            {"messages": None},
            {"messages": "hello"},
            {"messages": []},
        ]

        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaises(HTTPException) as context:
                    RequestProcessor.validate_request(body)
                self.assertEqual(context.exception.status_code, 400)

    def test_validate_request_rejects_invalid_message_shapes(self):
        invalid_bodies = [
            {"messages": ["bad"]},
            {"messages": [{"content": "missing role"}]},
            {"messages": [{"role": "user"}]},
        ]

        for body in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaises(HTTPException) as context:
                    RequestProcessor.validate_request(body)
                self.assertEqual(context.exception.status_code, 400)


class RequestProcessorHelperTests(ConfigIsolationMixin, unittest.TestCase):
    def test_normalize_model_id_strips_namespace_case_and_whitespace(self):
        self.assertEqual(normalize_model_id(" CodeBuddy/GLM-5.2 "), "glm-5.2")
        self.assertEqual(normalize_model_id(None), "")

    def test_strip_model_namespace_keeps_model_case(self):
        self.assertEqual(strip_model_namespace(" CodeBuddy/GLM-5.2 "), "GLM-5.2")
        self.assertEqual(strip_model_namespace(None), "")

    def test_should_configure_model_reasoning_uses_configured_names(self):
        config._config_cache["CODEBUDDY_FORCED_REASONING_MODELS"] = "codebuddy/glm-5.2"

        self.assertTrue(should_configure_model_reasoning("GLM-5.2"))
        self.assertFalse(should_configure_model_reasoning("lite"))

    def test_is_false_like_partitions_values(self):
        true_cases = [False, 0, "false", "0", "NO", "off", "disabled"]
        false_cases = [True, 1, "true", "yes", "", None]

        for value in true_cases:
            with self.subTest(value=value):
                self.assertTrue(is_false_like(value))
        for value in false_cases:
            with self.subTest(value=value):
                self.assertFalse(is_false_like(value))


if __name__ == "__main__":
    unittest.main()
