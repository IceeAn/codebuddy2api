import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import config

from src.codebuddy_api_client import codebuddy_api_client
from src.codebuddy_oauth import (
    get_auth_poll_headers,
    get_auth_start_headers,
    get_codebuddy_auth_state_endpoint,
)
from src.user_settings_store import UserSettingsStore

from tests.helpers import ConfigIsolationMixin


class ConfigTests(ConfigIsolationMixin, unittest.TestCase):
    def test_load_config_rejects_api_endpoint_outside_explicit_allowlist(self):
        with mock.patch.dict(
            "os.environ",
            {
                "CODEBUDDY_DATA_DIR": config.get_data_dir(),
                "CODEBUDDY_API_ENDPOINT": "https://copilot.tencent.com",
                "CODEBUDDY_ALLOWED_API_ENDPOINTS": "https://www.codebuddy.ai",
            },
        ):
            with self.assertRaisesRegex(ValueError, "CODEBUDDY_API_ENDPOINT"):
                config.load_config()

    def test_load_config_continues_when_dotenv_is_unavailable(self):
        real_import = __import__
        data_dir = config.get_data_dir()

        def import_without_dotenv(name, *args, **kwargs):
            if name == "dotenv":
                raise ImportError("dotenv unavailable")
            return real_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=import_without_dotenv),
            mock.patch.dict(
                "os.environ",
                {"CODEBUDDY_DATA_DIR": data_dir},
            ),
        ):
            config.load_config()

        self.assertEqual(config.get_data_dir(), data_dir)

    def test_config_helpers_cover_missing_and_invalid_values(self):
        self.assertIsNone(config._username_from_user(object()))
        self.assertEqual(config._parse_csv(None), [])

        config._config_cache["CODEBUDDY_ROTATION_COUNT"] = 0
        with self.assertRaisesRegex(ValueError, "positive integer"):
            config.get_rotation_count()
        with self.assertRaisesRegex(ValueError, "username is required"):
            config.update_settings({"CODEBUDDY_MODELS": "model"})

    def test_credentials_directory_is_fixed_below_data_directory(self):
        config._config_cache["CODEBUDDY_DATA_DIR"] = "/runtime/data"

        self.assertNotIn("CODEBUDDY_CREDS_DIR", config._DEFAULT_CONFIG)
        self.assertEqual(
            config.get_codebuddy_creds_dir(),
            "/runtime/data/credentials",
        )

    def test_relative_data_directory_is_resolved_from_application_root(self):
        config._config_cache["CODEBUDDY_DATA_DIR"] = "relative-data"
        application_root = Path(config.__file__).resolve().parent

        with mock.patch(
            "src.sqlite_database.Path.cwd",
            return_value=Path("/unrelated-working-directory"),
        ):
            self.assertEqual(
                config.get_data_dir(),
                str(application_root / "relative-data"),
            )
            self.assertEqual(
                config.get_database_path(),
                application_root / "relative-data" / "codebuddy2api.sqlite3",
            )
            self.assertEqual(
                config.get_codebuddy_creds_dir(),
                str(application_root / "relative-data" / "credentials"),
            )

    def test_unsafe_api_endpoint_fails_closed(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://evil.example"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com,https://www.codebuddy.ai"

        with self.assertRaisesRegex(ValueError, "CODEBUDDY_API_ENDPOINT"):
            config.get_codebuddy_api_endpoint()

    def test_api_endpoint_normalizes_case_and_trailing_slash(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://COPILOT.TENCENT.COM/"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://copilot.tencent.com"

        self.assertEqual(config.get_codebuddy_api_endpoint(), "https://copilot.tencent.com")

    def test_base_url_normalization_rejects_unsafe_shapes_and_preserves_port(self):
        self.assertEqual(
            config._normalize_base_url("https://EXAMPLE.com:8443/base/"),
            "https://example.com:8443/base",
        )
        self.assertEqual(
            config._normalize_base_url("https://[2001:DB8::1]:8443/base/"),
            "https://[2001:db8::1]:8443/base",
        )
        for value in (
            "https://example.com:invalid",
            "http://example.com",
            "https:///missing-host",
            "https://user:password@example.com",
            "https://example.com?query=1",
            "https://example.com#fragment",
            "https://example.com/\x00path",
            "https://example.com/\x7fpath",
            "https://example.com\r\n",
        ):
            with self.subTest(value=value):
                self.assertEqual(config._normalize_base_url(value), "")

    def test_invalid_api_endpoint_shape_fails_closed(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "not-a-url"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://www.codebuddy.ai"

        with self.assertRaisesRegex(ValueError, "valid HTTPS"):
            config.get_codebuddy_api_endpoint()

    def test_allowed_api_endpoints_rejects_invalid_values(self):
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "http://bad,not-a-url,https://www.codebuddy.ai"

        with self.assertRaisesRegex(ValueError, "CODEBUDDY_ALLOWED_API_ENDPOINTS"):
            config.get_allowed_api_endpoints()

    def test_allowed_api_endpoints_normalizes_and_deduplicates_explicit_values(self):
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = (
            "https://WWW.CODEBUDDY.AI/,https://www.codebuddy.ai"
        )

        self.assertEqual(config.get_allowed_api_endpoints(), ["https://www.codebuddy.ai"])

    def test_empty_api_endpoint_allowlist_fails_closed(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://www.codebuddy.ai"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = ""

        with self.assertRaisesRegex(ValueError, "CODEBUDDY_ALLOWED_API_ENDPOINTS"):
            config.get_codebuddy_api_endpoint()

    def test_default_models_keep_minimal_static_fallback(self):
        config._config_cache["CODEBUDDY_MODELS"] = ",".join(config.DEFAULT_CODEBUDDY_MODELS)

        models = config.get_available_models()

        self.assertEqual(models, ["glm-5.2", "deepseek-v4-pro"])

    def test_parse_model_csv_filters_empty_entries_and_preserves_ordered_uniqueness(self):
        config._config_cache["CODEBUDDY_MODELS"] = "glm-5.2,, lite,glm-5.2,lite "

        self.assertEqual(config.get_available_models(), ["glm-5.2", "lite"])

    def test_forced_reasoning_models_filters_empty_entries(self):
        config._config_cache["CODEBUDDY_FORCED_REASONING_MODELS"] = "glm-5.2,, lite "

        self.assertEqual(config.get_forced_reasoning_models(), ["glm-5.2", "lite"])

    def test_forced_temperature_accepts_bounded_numbers_and_empty_values(self):
        cases = [
            ("1", 1),
            ("0.7", 0.7),
            (2, 2),
            (0, 0),
            ("", None),
            (None, None),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                config._config_cache["CODEBUDDY_FORCED_TEMPERATURE"] = value
                self.assertEqual(config.get_forced_temperature(), expected)

    def test_forced_temperature_rejects_invalid_non_finite_and_out_of_range_values(self):
        for value in ("not-a-number", "nan", "inf", -0.1, 2.1, True):
            with self.subTest(value=value):
                config._config_cache["CODEBUDDY_FORCED_TEMPERATURE"] = value
                with self.assertRaisesRegex(ValueError, "CODEBUDDY_FORCED_TEMPERATURE"):
                    config.get_forced_temperature()

    def test_startup_boolean_and_log_level_values_are_strict(self):
        boolean_cases = (
            ("CODEBUDDY_SSL_VERIFY", config.get_ssl_verify),
            ("CODEBUDDY_AUTO_ROTATION_ENABLED", config.get_auto_rotation_enabled),
        )
        for key, getter in boolean_cases:
            for value, expected in (("true", True), ("1", True), ("false", False), ("0", False)):
                with self.subTest(key=key, value=value):
                    config._config_cache[key] = value
                    self.assertIs(getter(), expected)
            for invalid in ("sometimes", 1):
                config._config_cache[key] = invalid
                with self.assertRaisesRegex(ValueError, key):
                    getter()

        for value in ("debug", "INFO", "warning", "ERROR", "critical"):
            with self.subTest(log_level=value):
                config._config_cache["CODEBUDDY_LOG_LEVEL"] = value
                self.assertEqual(config.get_log_level(), value.upper())
        config._config_cache["CODEBUDDY_LOG_LEVEL"] = "TRACE"
        with self.assertRaisesRegex(ValueError, "CODEBUDDY_LOG_LEVEL"):
            config.get_log_level()

        for value, expected in (("1", 1), (8001, 8001), ("65535", 65535)):
            with self.subTest(port=value):
                config._config_cache["CODEBUDDY_PORT"] = value
                self.assertEqual(config.get_server_port(), expected)
        for value in ("invalid", 0, -1, 65536, True):
            with self.subTest(port=value):
                config._config_cache["CODEBUDDY_PORT"] = value
                with self.assertRaisesRegex(ValueError, "CODEBUDDY_PORT"):
                    config.get_server_port()

    def test_load_config_validates_all_strict_startup_values(self):
        cases = {
            "CODEBUDDY_PORT": "invalid",
            "CODEBUDDY_SSL_VERIFY": "maybe",
            "CODEBUDDY_LOG_LEVEL": "TRACE",
            "CODEBUDDY_FORCED_TEMPERATURE": "nan",
            "CODEBUDDY_STRIP_MODEL_NAMESPACE": "maybe",
            "CODEBUDDY_AUTO_ROTATION_ENABLED": "maybe",
            "CODEBUDDY_ROTATION_COUNT": "1.5",
            "CODEBUDDY_MAX_REQUEST_BODY_BYTES": "0",
            "CODEBUDDY_LOGIN_RATE_WINDOW_SECONDS": "1.5",
            "CODEBUDDY_LOGIN_GLOBAL_MAX_ATTEMPTS": "0",
            "CODEBUDDY_LOGIN_IP_MAX_ATTEMPTS": "-1",
            "CODEBUDDY_LOGIN_USERNAME_MAX_ATTEMPTS": "invalid",
            "CODEBUDDY_LOGIN_MAX_CONCURRENCY": "0",
            "CODEBUDDY_MAX_CONCURRENT_REQUESTS": "0",
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                with mock.patch.dict(
                    "os.environ",
                    {"CODEBUDDY_DATA_DIR": config.get_data_dir(), key: value},
                    clear=True,
                ):
                    with self.assertRaisesRegex(ValueError, key):
                        config.load_config()

    def test_security_limit_defaults_and_optional_global_concurrency(self):
        self.assertEqual(config.get_max_request_body_bytes(), 16 * 1024 * 1024)
        self.assertEqual(config.get_login_rate_window_seconds(), 60)
        self.assertEqual(config.get_login_global_max_attempts(), 60)
        self.assertEqual(config.get_login_ip_max_attempts(), 10)
        self.assertEqual(config.get_login_username_max_attempts(), 5)
        self.assertEqual(config.get_login_max_concurrency(), 2)
        self.assertIsNone(config.get_max_concurrent_requests())

        config._config_cache["CODEBUDDY_MAX_CONCURRENT_REQUESTS"] = "100"
        self.assertEqual(config.get_max_concurrent_requests(), 100)

        config._config_cache["CODEBUDDY_MAX_CONCURRENT_REQUESTS"] = True
        with self.assertRaisesRegex(ValueError, "positive integer"):
            config.get_max_concurrent_requests()

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
        with sqlite3.connect(config.get_database_path()) as connection:
            persisted = dict(connection.execute(
                "SELECT setting_key, value_json FROM user_settings WHERE username = ?",
                ("admin",),
            ))
        self.assertEqual(persisted["CODEBUDDY_MODELS"], '"admin-only"')
        self.assertNotIn("CODEBUDDY_LOG_LEVEL", persisted)

    def test_concurrent_settings_updates_keep_database_and_cache_consistent(self):
        first_write_committed = threading.Event()
        release_first_write = threading.Event()
        second_lock_attempted = threading.Event()
        second_write_entered_store = threading.Event()
        original_update = UserSettingsStore.update
        update_context = threading.local()

        class ObservableRLock:
            def __init__(self):
                self._lock = threading.RLock()

            def __enter__(self):
                if getattr(update_context, "value", None) == "second":
                    second_lock_attempted.set()
                self._lock.acquire()
                return self

            def __exit__(self, _exc_type, _exc_value, _traceback):
                self._lock.release()

        def controlled_update(store, username, values):
            original_update(store, username, values)
            if values["CODEBUDDY_MODELS"] == "first":
                first_write_committed.set()
                if not release_first_write.wait(timeout=2):
                    raise TimeoutError("first settings write was not released")
            else:
                second_write_entered_store.set()

        def update_models(value):
            update_context.value = value
            config.update_settings(
                {"CODEBUDDY_MODELS": value},
                username="admin",
            )

        with (
            mock.patch.object(UserSettingsStore, "update", controlled_update),
            mock.patch.object(config, "_USER_SETTINGS_LOCK", ObservableRLock()),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first_update = executor.submit(update_models, "first")
            try:
                self.assertTrue(first_write_committed.wait(timeout=2))
                second_update = executor.submit(update_models, "second")
                self.assertTrue(second_lock_attempted.wait(timeout=2))
                self.assertFalse(second_write_entered_store.is_set())
            finally:
                release_first_write.set()

            first_update.result(timeout=2)
            second_update.result(timeout=2)

        with sqlite3.connect(config.get_database_path()) as connection:
            persisted = connection.execute(
                "SELECT value_json FROM user_settings WHERE username = ? AND setting_key = ?",
                ("admin", "CODEBUDDY_MODELS"),
            ).fetchone()[0]
        self.assertEqual(persisted, '"second"')
        self.assertEqual(config.get_available_models("admin"), ["second"])

    def test_initialize_database_creates_empty_schema_without_persisting_defaults(self):
        config.initialize_database()

        self.assertEqual(config.get_available_models("new-user"), list(config.DEFAULT_CODEBUDDY_MODELS))
        with sqlite3.connect(config.get_database_path()) as connection:
            settings_count = connection.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0]
            api_keys_count = connection.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]

        self.assertEqual(settings_count, 0)
        self.assertEqual(api_keys_count, 0)

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

    def test_load_config_reloads_user_settings_from_database(self):
        config.update_settings({"CODEBUDDY_MODELS": "persisted-model"}, username="admin")
        config._user_settings_cache = {}

        with mock.patch.dict(
            "os.environ",
            {"CODEBUDDY_DATA_DIR": config.get_data_dir()},
        ):
            config.load_config()

        self.assertEqual(config.get_available_models("admin"), ["persisted-model"])


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
        self.assertEqual(headers["X-User-Id"], "user-id")
        self.assertNotIn("X-Enterprise-Id", headers)
        self.assertNotIn("X-Tenant-Id", headers)

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
            enterprise_id="enterprise-1",
            conversation_id="conv",
            conversation_request_id="req",
            conversation_message_id="msg",
            request_id="request",
        )

        self.assertEqual(headers["X-Conversation-ID"], "conv")
        self.assertEqual(headers["X-Conversation-Request-ID"], "req")
        self.assertEqual(headers["X-Conversation-Message-ID"], "msg")
        self.assertEqual(headers["X-Request-ID"], "request")
        self.assertEqual(headers["X-Enterprise-Id"], "enterprise-1")
        self.assertEqual(headers["X-Tenant-Id"], "enterprise-1")

    def test_codebuddy_auth_endpoint_follows_configured_china_endpoint(self):
        self.assertEqual(
            get_codebuddy_auth_state_endpoint(),
            "https://copilot.tencent.com/v2/plugin/auth/state",
        )
        self.assertEqual(get_auth_start_headers()["Host"], "copilot.tencent.com")
        self.assertEqual(get_auth_poll_headers()["X-Domain"], "copilot.tencent.com")


if __name__ == "__main__":
    unittest.main()
