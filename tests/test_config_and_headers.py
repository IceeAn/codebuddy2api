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

    def test_default_models_follow_china_endpoint(self):
        config._config_cache["CODEBUDDY_MODELS"] = ",".join(config.DEFAULT_CODEBUDDY_MODELS)

        models = config.get_available_models()

        self.assertEqual(models[0], "glm-5.2")
        self.assertIn("deepseek-v4-pro", models)
        self.assertNotIn("auto-chat", models)

    def test_parse_model_csv_preserves_current_behavior_for_empty_entries(self):
        config._config_cache["CODEBUDDY_MODELS"] = "glm-5.2,, lite "

        self.assertEqual(config.get_available_models(), ["glm-5.2", "", "lite"])

    def test_update_settings_ignores_non_hot_reloadable_keys(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            settings_path = f"{tmp_dir}/config.json"
            with mock.patch.object(config, "_CONFIG_JSON_PATH", settings_path):
                config.update_settings({
                    "CODEBUDDY_LOG_LEVEL": "debug",
                    "CODEBUDDY_ALLOWED_HOSTS": "evil.example",
                })

                self.assertEqual(config.get_log_level(), "DEBUG")
                self.assertNotEqual(config.get_allowed_hosts(), ["evil.example"])
                with open(settings_path, "r", encoding="utf-8") as f:
                    persisted = json.load(f)
                self.assertNotIn("CODEBUDDY_ALLOWED_HOSTS", persisted)


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
