import json
import tempfile
import unittest
from unittest import mock

import config

from src.codebuddy_api_client import codebuddy_api_client
from src.codebuddy_oauth import (
    get_auth_poll_headers,
    get_auth_start_headers,
    get_codebuddy_auth_state_endpoint,
)

from tests.helpers import ConfigIsolationMixin


class ConfigTests(ConfigIsolationMixin, unittest.TestCase):
    def test_unsafe_api_endpoint_falls_back_to_default(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://evil.example"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

        self.assertEqual(config.get_codebuddy_api_endpoint(), "https://copilot.tencent.com")

    def test_api_endpoint_normalizes_case_and_trailing_slash(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://COPILOT.TENCENT.COM/"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com"

        self.assertEqual(config.get_codebuddy_api_endpoint(), "https://copilot.tencent.com")

    def test_allowed_api_endpoints_filters_invalid_values_and_keeps_default(self):
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "http://bad,not-a-url,https://www.codebuddy.ai"

        self.assertEqual(
            config.get_allowed_api_endpoints(),
            ["https://www.codebuddy.ai", "https://copilot.tencent.com"],
        )

    def test_default_models_keep_minimal_static_fallback(self):
        config._config_cache["CODEBUDDY_MODELS"] = ",".join(config.DEFAULT_CODEBUDDY_MODELS)

        models = config.get_available_models()

        self.assertEqual(models, ["glm-5.2", "deepseek-v4-pro"])

    def test_parse_model_csv_preserves_current_behavior_for_empty_entries(self):
        config._config_cache["CODEBUDDY_MODELS"] = "glm-5.2,, lite "

        self.assertEqual(config.get_available_models(), ["glm-5.2", "", "lite"])

    def test_forced_reasoning_models_filters_empty_entries(self):
        config._config_cache["CODEBUDDY_FORCED_REASONING_MODELS"] = "glm-5.2,, lite "

        self.assertEqual(config.get_forced_reasoning_models(), ["glm-5.2", "lite"])

    def test_forced_temperature_accepts_int_float_empty_and_invalid_classes(self):
        cases = [
            ("1", 1),
            ("0.7", 0.7),
            (2, 2),
            ("", None),
            (None, None),
            ("not-a-number", None),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                config._config_cache["CODEBUDDY_FORCED_TEMPERATURE"] = value
                self.assertEqual(config.get_forced_temperature(), expected)

    def test_strip_model_namespace_defaults_to_enabled_but_empty_disables(self):
        self.assertIs(config._DEFAULT_CONFIG["CODEBUDDY_STRIP_MODEL_NAMESPACE"], True)

        cases = [
            (True, True),
            ("true", True),
            ("1", True),
            (False, False),
            ("", False),
            (None, False),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                config._config_cache["CODEBUDDY_STRIP_MODEL_NAMESPACE"] = value
                self.assertIs(config.get_strip_model_namespace(), expected)

    def test_update_settings_persists_user_scoped_settings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = f"{tmp_dir}/config.json"
            with mock.patch.object(config, "_CONFIG_JSON_PATH", settings_path):
                config.update_settings(
                    {
                        "CODEBUDDY_MODELS": "admin-only",
                        "CODEBUDDY_AUTO_ROTATION_ENABLED": False,
                        "CODEBUDDY_ROTATION_COUNT": 2,
                    },
                    username="admin",
                )

                self.assertEqual(config.get_available_models("admin"), ["admin-only"])
                self.assertIs(config.get_auto_rotation_enabled("admin"), False)
                self.assertEqual(config.get_rotation_count("admin"), 2)
                with open(settings_path, "r", encoding="utf-8") as f:
                    persisted = json.load(f)
                self.assertEqual(persisted["users"]["admin"]["CODEBUDDY_MODELS"], "admin-only")
                self.assertNotIn("CODEBUDDY_LOG_LEVEL", persisted["users"]["admin"])

    def test_get_editable_config_returns_typed_default_and_environment_values(self):
        config._config_cache["CODEBUDDY_FORCED_TEMPERATURE"] = "1"
        config._config_cache["CODEBUDDY_AUTO_ROTATION_ENABLED"] = "false"
        config._config_cache["CODEBUDDY_STRIP_MODEL_NAMESPACE"] = "true"
        config._config_cache["CODEBUDDY_ROTATION_COUNT"] = "3"

        settings = config.get_editable_config(username="new-user")

        self.assertEqual(settings["CODEBUDDY_FORCED_TEMPERATURE"], 1)
        self.assertIs(settings["CODEBUDDY_AUTO_ROTATION_ENABLED"], False)
        self.assertIs(settings["CODEBUDDY_STRIP_MODEL_NAMESPACE"], True)
        self.assertEqual(settings["CODEBUDDY_ROTATION_COUNT"], 3)

    def test_get_editable_config_returns_null_for_empty_nullable_temperature(self):
        config._config_cache["CODEBUDDY_FORCED_TEMPERATURE"] = ""

        settings = config.get_editable_config(username="new-user")

        self.assertIsNone(settings["CODEBUDDY_FORCED_TEMPERATURE"])

    def test_update_settings_rejects_non_user_settings_and_zero_rotation_count(self):
        invalid_payloads = [
            {"CODEBUDDY_LOG_LEVEL": "debug"},
            {"CODEBUDDY_ALLOWED_HOSTS": "evil.example"},
            {"CODEBUDDY_ROTATION_COUNT": 0},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    config.update_settings(payload, username="admin")

    def test_load_config_rejects_flat_config_without_user_scope(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = f"{tmp_dir}/config.json"
            flat_config = {
                "CODEBUDDY_LOG_LEVEL": "DEBUG",
                "CODEBUDDY_MODELS": "flat-model",
                "CODEBUDDY_ROTATION_COUNT": 0,
                "CODEBUDDY_ALLOWED_HOSTS": "evil.example",
            }
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(flat_config, f)

            with mock.patch.object(config, "_CONFIG_JSON_PATH", settings_path):
                with self.assertRaises(ValueError):
                    config.load_config()

            with open(settings_path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), flat_config)


class CodeBuddyHeaderTests(ConfigIsolationMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://copilot.tencent.com"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

    def test_codebuddy_headers_follow_configured_china_endpoint(self):
        headers = codebuddy_api_client.generate_codebuddy_headers("token-value", "user-id")

        self.assertEqual(headers["Host"], "copilot.tencent.com")
        self.assertEqual(headers["X-Domain"], "copilot.tencent.com")
        self.assertEqual(headers["X-IDE-Version"], "2.107.0")
        self.assertEqual(headers["User-Agent"], "CLI/2.107.0 CodeBuddy/2.107.0")
        self.assertEqual(headers["x-stainless-package-version"], "6.25.0")
        self.assertEqual(headers["x-stainless-runtime-version"], "v24.11.1")
        self.assertEqual(headers["X-Agent-Purpose"], "conversation")
        self.assertEqual(headers["X-Private-Data"], "false")
        self.assertEqual(headers["X-CodeBuddy-Request"], "1")

    def test_codebuddy_headers_use_credential_domain_when_available(self):
        headers = codebuddy_api_client.generate_codebuddy_headers(
            "token-value",
            "user-id",
            domain="www.codebuddy.cn",
        )

        self.assertEqual(headers["Host"], "copilot.tencent.com")
        self.assertEqual(headers["X-Domain"], "www.codebuddy.cn")

    def test_codebuddy_headers_reject_unsafe_credential_domain(self):
        headers = codebuddy_api_client.generate_codebuddy_headers(
            "token-value",
            "user-id",
            domain="www.codebuddy.cn\r\nX-Evil: true",
        )

        self.assertEqual(headers["X-Domain"], "copilot.tencent.com")

    def test_codebuddy_headers_preserve_supplied_conversation_ids(self):
        headers = codebuddy_api_client.generate_codebuddy_headers(
            "token-value",
            "user-id",
            conversation_id="conv",
            conversation_request_id="req",
            conversation_message_id="msg",
            request_id="request",
        )

        self.assertEqual(headers["X-Conversation-ID"], "conv")
        self.assertEqual(headers["X-Conversation-Request-ID"], "req")
        self.assertEqual(headers["X-Conversation-Message-ID"], "msg")
        self.assertEqual(headers["X-Request-ID"], "request")

    def test_codebuddy_auth_endpoint_follows_configured_china_endpoint(self):
        self.assertEqual(
            get_codebuddy_auth_state_endpoint(),
            "https://copilot.tencent.com/v2/plugin/auth/state",
        )
        self.assertEqual(get_auth_start_headers()["Host"], "copilot.tencent.com")
        self.assertEqual(get_auth_poll_headers()["X-Domain"], "copilot.tencent.com")


if __name__ == "__main__":
    unittest.main()
