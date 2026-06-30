import unittest
from unittest import mock

import config
from fastapi import HTTPException

from src.admin_router import (
    AdminSettingsUpdate,
    ApiKeyCreateRequest,
    CredentialCreateRequest,
    CredentialTestRequest,
    create_admin_api_key,
    create_admin_credential,
    delete_admin_api_key,
    delete_admin_credential,
    get_admin_settings,
    get_admin_status,
    list_admin_api_keys,
    list_admin_credentials,
    save_admin_settings,
    select_admin_credential,
    test_admin_credential,
)
from src.auth_types import AuthenticatedUser
from src.codebuddy_token_manager import CodeBuddyTokenManagerRegistry
from src.usage_stats_manager import usage_stats_manager

from tests.helpers import TempConfigMixin, configure_users_file, make_request


class AdminApiTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        config._config_cache["CODEBUDDY_CREDS_DIR"] = str(self.temp_path / "creds")
        self.user = AuthenticatedUser(username="admin", source="session_cookie")
        self.registry = CodeBuddyTokenManagerRegistry()
        usage_stats_manager._reset_for_tests()

    def _manager(self):
        return self.registry.for_user(self.user)

    async def test_admin_status_uses_real_usage_and_credential_state(self):
        manager = self._manager()
        self.assertTrue(manager.add_credential("token-value", "upstream-user", "primary"))

        with (
            mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
            mock.patch("src.admin_router.SERVICE_STARTED_MONOTONIC", 100.0),
            mock.patch("src.admin_router.time.monotonic", return_value=165.8),
        ):
            result = await get_admin_status(make_request(path="/api/admin/status"), self.user)

        self.assertEqual(result["service"], "CodeBuddy2API")
        self.assertEqual(result["username"], "admin")
        self.assertEqual(result["uptime_seconds"], 65)
        self.assertNotIn("server_time", result)
        self.assertEqual(result["api_base_url"], "http://testserver/codebuddy/v1")
        self.assertEqual(result["credentials"]["total"], 1)
        self.assertEqual(result["credentials"]["valid"], 1)
        self.assertEqual(result["credentials"]["current"]["credential_id"], manager.get_credentials_info()[0]["credential_id"])

    async def test_admin_api_keys_are_session_scoped(self):
        created = await create_admin_api_key(ApiKeyCreateRequest(name="opencode"), self.user)

        self.assertTrue(created["api_key"].startswith("sk-"))
        listed = await list_admin_api_keys(self.user)
        self.assertEqual(listed["api_keys"][0]["preview"], created["preview"])
        self.assertNotIn("api_key", listed["api_keys"][0])

        deleted = await delete_admin_api_key(created["id"], self.user)
        self.assertTrue(deleted["deleted"])
        self.assertEqual((await list_admin_api_keys(self.user))["api_keys"], [])

    async def test_admin_credentials_use_stable_id_not_index(self):
        created = await create_admin_credential(
            CredentialCreateRequest(bearer_token="token-value", user_id="upstream-user"),
            self.user,
        )
        credential_id = created["credential"]["credential_id"]

        listed = await list_admin_credentials(self.user)
        self.assertEqual(listed["credentials"][0]["credential_id"], credential_id)
        self.assertNotIn("bearer_token", listed["credentials"][0])

        selected = await select_admin_credential(credential_id, self.user)
        self.assertEqual(selected["current"]["credential_id"], credential_id)
        self.assertEqual(selected["current"]["status"], "auto_rotation_disabled")
        self.assertIs(selected["current"]["auto_rotation_enabled"], False)
        self.assertIs(selected["auto_rotation_disabled_by_select"], True)
        self.assertNotIn("message", selected)

        selected_again = await select_admin_credential(credential_id, self.user)
        self.assertIs(selected_again["auto_rotation_disabled_by_select"], False)
        self.assertNotIn("message", selected_again)

        deleted = await delete_admin_credential(credential_id, self.user)
        self.assertTrue(deleted["deleted"])
        self.assertEqual((await list_admin_credentials(self.user))["credentials"], [])

    async def test_create_admin_credential_fails_when_new_credential_cannot_be_identified(self):
        manager = mock.Mock()
        manager.get_credentials_info.return_value = [{"credential_id": "existing-id"}]
        manager.add_credential.return_value = True

        with (
            mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
            mock.patch(
                "src.admin_router._safe_credentials",
                return_value=[{"credential_id": "existing-id"}],
            ) as safe_credentials,
        ):
            with self.assertRaises(HTTPException) as context:
                await create_admin_credential(
                    CredentialCreateRequest(bearer_token="new-token", user_id="upstream-user"),
                    self.user,
                )

        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(context.exception.detail, "Failed to identify newly created credential")
        safe_credentials.assert_called_once_with(manager)

    async def test_create_admin_credential_returns_new_row_when_numeric_filename_has_gap(self):
        manager = self._manager()
        self.assertTrue(manager.add_credential("first-token", "first-user", "codebuddy_token_1.json"))
        self.assertTrue(manager.add_credential("second-token", "second-user", "codebuddy_token_2.json"))
        self.assertTrue(manager.delete_credential_by_id(manager.get_credentials_info()[0]["credential_id"]))

        with mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager):
            result = await create_admin_credential(
                CredentialCreateRequest(bearer_token="third-token", user_id="third-user"),
                self.user,
            )
            listed = await list_admin_credentials(self.user)

        self.assertEqual(result["credential"]["user_id"], "third-user")
        self.assertEqual(result["credential"]["filename"], "codebuddy_token_3.json")
        credentials = {item["filename"]: item for item in listed["credentials"]}
        self.assertIn("codebuddy_token_2.json", credentials)
        self.assertIn("codebuddy_token_3.json", credentials)

    async def test_admin_status_only_returns_current_user_usage_stats(self):
        usage_stats_manager.record_model_usage("alice", "alice-model")
        usage_stats_manager.record_credential_usage("alice", "alice-credential.json")
        usage_stats_manager.record_model_usage("admin", "admin-model")
        usage_stats_manager.record_credential_usage("admin", "admin-credential.json")

        result = await get_admin_status(make_request(path="/api/admin/status"), self.user)

        self.assertEqual(result["usage"], {
            "model_usage": {"admin-model": 1},
            "credential_usage": {"admin-credential.json": 1},
        })

    async def test_admin_credential_test_uses_selected_row_credential_and_models(self):
        manager = self._manager()
        self.assertTrue(manager.add_credential("current-token", "current-user", "current"))
        self.assertTrue(manager.add_credential("target-token", "target-user", "target"))
        credential_id = manager.get_credentials_info()[1]["credential_id"]
        captured = {}

        class FakeService:
            async def handle_non_stream_response(self, payload, headers):
                captured["payload"] = payload
                captured["headers"] = headers
                return {"choices": [{"message": {"content": "ok"}}]}

        with (
            mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
            mock.patch(
                "src.admin_router.models_manager.get_first_actual_model_for_credential",
                mock.AsyncMock(return_value="target-real-model"),
            ) as first_model_for_credential,
            mock.patch(
                "src.models_manager.ModelsManager.get_first_actual_model",
                mock.AsyncMock(side_effect=AssertionError("should not use current credential model lookup")),
            ) as first_model,
        ):
            result = await test_admin_credential(
                credential_id,
                CredentialTestRequest(**{"model": "client-model"}),
                self.user,
                stream_service_factory=FakeService,
            )

        self.assertTrue(result["ok"])
        first_model_for_credential.assert_awaited_once()
        called_user, called_credential_id, called_credential = first_model_for_credential.await_args.args
        self.assertEqual(called_user, self.user)
        self.assertEqual(called_credential_id, credential_id)
        self.assertEqual(called_credential["bearer_token"], "target-token")
        first_model.assert_not_awaited()
        self.assertEqual(captured["headers"]["Authorization"], "Bearer target-token")
        self.assertEqual(captured["payload"]["model"], "target-real-model")
        self.assertIs(captured["payload"]["stream"], True)

    async def test_admin_credential_test_returns_ok_false_when_model_lookup_fails(self):
        manager = self._manager()
        self.assertTrue(manager.add_credential("target-token", "target-user", "target"))
        credential_id = manager.get_credentials_info()[0]["credential_id"]

        class FakeService:
            async def handle_non_stream_response(self, _payload, _headers):
                raise AssertionError("stream service should not run without a model")

        with (
            mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
            mock.patch(
                "src.admin_router.models_manager.get_first_actual_model_for_credential",
                mock.AsyncMock(side_effect=RuntimeError("config api unavailable")),
            ),
        ):
            result = await test_admin_credential(
                credential_id,
                CredentialTestRequest(message="hello"),
                self.user,
                stream_service_factory=FakeService,
            )

        self.assertIs(result["ok"], False)
        self.assertEqual(result["status_code"], 502)
        self.assertIn("config api unavailable", result["detail"])

    async def test_missing_admin_credential_returns_404(self):
        with self.assertRaises(HTTPException) as context:
            await delete_admin_credential("missing", self.user)

        self.assertEqual(context.exception.status_code, 404)

    async def test_settings_contract_returns_typed_fields_and_saves_hot_reloadable_values(self):
        settings = await get_admin_settings(self.user)

        field_by_key = {field["key"]: field for field in settings["fields"]}
        self.assertNotIn("CODEBUDDY_LOG_LEVEL", field_by_key)
        self.assertEqual(field_by_key["CODEBUDDY_AUTO_ROTATION_ENABLED"]["type"], "boolean")
        self.assertEqual(field_by_key["CODEBUDDY_AUTO_ROTATION_ENABLED"]["label"], "凭证轮换")
        self.assertEqual(field_by_key["CODEBUDDY_STRIP_MODEL_NAMESPACE"]["type"], "boolean")
        self.assertEqual(field_by_key["CODEBUDDY_ROTATION_COUNT"]["type"], "number")
        self.assertEqual(field_by_key["CODEBUDDY_ROTATION_COUNT"]["min"], 1)

        with mock.patch.object(config, "_CONFIG_JSON_PATH", str(self.temp_path / "config.json")):
            result = await save_admin_settings(
                AdminSettingsUpdate(settings={
                    "CODEBUDDY_MODELS": "admin-only",
                    "CODEBUDDY_AUTO_ROTATION_ENABLED": False,
                    "CODEBUDDY_ROTATION_COUNT": 3,
                }),
                self.user,
            )

        self.assertEqual(result["settings"]["CODEBUDDY_MODELS"], "admin-only")
        self.assertIs(result["settings"]["CODEBUDDY_AUTO_ROTATION_ENABLED"], False)
        self.assertEqual(result["settings"]["CODEBUDDY_ROTATION_COUNT"], 3)
        self.assertNotIn("CODEBUDDY_LOG_LEVEL", result["settings"])
        self.assertNotIn("CODEBUDDY_ALLOWED_HOSTS", result["settings"])

    async def test_settings_are_isolated_by_authenticated_user(self):
        alice = AuthenticatedUser(username="alice", source="session_cookie")

        await save_admin_settings(
            AdminSettingsUpdate(settings={"CODEBUDDY_MODELS": "admin-only"}),
            self.user,
        )

        admin_settings = await get_admin_settings(self.user)
        alice_settings = await get_admin_settings(alice)

        self.assertEqual(admin_settings["settings"]["CODEBUDDY_MODELS"], "admin-only")
        self.assertEqual(
            alice_settings["settings"]["CODEBUDDY_MODELS"],
            ",".join(config.DEFAULT_CODEBUDDY_MODELS),
        )

        await save_admin_settings(
            AdminSettingsUpdate(settings={"CODEBUDDY_MODELS": "alice-only"}),
            alice,
        )

        self.assertEqual((await get_admin_settings(self.user))["settings"]["CODEBUDDY_MODELS"], "admin-only")
        self.assertEqual((await get_admin_settings(alice))["settings"]["CODEBUDDY_MODELS"], "alice-only")

    async def test_settings_reject_log_level_and_zero_rotation_count(self):
        invalid_payloads = [
            {"CODEBUDDY_LOG_LEVEL": "DEBUG"},
            {"CODEBUDDY_ROTATION_COUNT": 0},
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(HTTPException) as context:
                    await save_admin_settings(AdminSettingsUpdate(settings=payload), self.user)
                self.assertEqual(context.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
