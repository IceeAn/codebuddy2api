import base64
import json
import unittest
from unittest import mock

import config
from fastapi import HTTPException
from pydantic import ValidationError

from src.admin_router import (
    AdminSettingsUpdate,
    ApiKeyCreateRequest,
    CredentialCreateRequest,
    CredentialTestRequest,
    _safe_credential,
    _safe_credentials,
    _time_remaining_text,
    create_admin_api_key,
    create_admin_credential,
    delete_admin_api_key,
    delete_admin_credential,
    get_admin_settings,
    get_admin_status,
    get_credential_test_stats_context,
    get_stream_service_factory,
    list_admin_api_keys,
    list_admin_credentials,
    save_admin_settings,
    select_admin_credential,
    test_admin_credential,
    test_admin_credential_route,
    toggle_admin_auto_rotation,
)
from src.auth_types import AuthenticatedUser
from src.codebuddy_token_manager import CodeBuddyTokenManagerRegistry

from tests.helpers import TempConfigMixin, configure_users_file, make_request


def jwt_with_payload(payload):
    encoded_payload = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    return f"header.{encoded_payload}.signature"


class AdminApiTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.user = AuthenticatedUser(username="admin", source="session_cookie")
        self.registry = CodeBuddyTokenManagerRegistry()

    def _manager(self):
        return self.registry.for_user(self.user)

    def _add_credential(self, manager, bearer_token, user_id, filename, **extra):
        credential_data = {"bearer_token": bearer_token, "user_id": user_id}
        credential_data.update(extra)
        return manager.add_credential_with_data(credential_data, filename)

    async def test_admin_status_uses_real_usage_and_credential_state(self):
        manager = self._manager()
        self.assertTrue(self._add_credential(manager, "token-value", "upstream-user", "primary"))

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
        self.assertEqual(result["api_base_url"], "http://testserver/openai/v1")
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

    async def test_delete_missing_admin_api_key_returns_404(self):
        with self.assertRaises(HTTPException) as context:
            await delete_admin_api_key("missing", self.user)

        self.assertEqual(context.exception.status_code, 404)

    async def test_admin_credentials_use_stable_id_not_index(self):
        created = await create_admin_credential(
            CredentialCreateRequest(bearer_token=jwt_with_payload({"sub": "upstream-user"})),
            self.user,
        )
        credential_id = created["credential"]["credential_id"]

        listed = await list_admin_credentials(self.user)
        self.assertEqual(listed["credentials"][0]["credential_id"], credential_id)
        self.assertEqual(listed["credentials"][0]["user_id"], "upstream-user")
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
                    CredentialCreateRequest(bearer_token=jwt_with_payload({"sub": "upstream-user"})),
                    self.user,
                )

        self.assertEqual(context.exception.status_code, 500)
        self.assertEqual(context.exception.detail, "Failed to identify newly created credential")
        safe_credentials.assert_called_once_with(manager)

    async def test_create_admin_credential_reports_store_failure(self):
        manager = mock.Mock()
        manager.get_credentials_info.return_value = []
        manager.add_credential.return_value = False
        with mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager):
            with self.assertRaises(HTTPException) as save_context:
                await create_admin_credential(
                    CredentialCreateRequest(bearer_token="token"),
                    self.user,
                )
        self.assertEqual(save_context.exception.status_code, 500)

    def test_credential_create_request_rejects_blank_token(self):
        for bearer_token in ("", " ", "\t"):
            with self.subTest(bearer_token=bearer_token):
                with self.assertRaises(ValidationError):
                    CredentialCreateRequest(bearer_token=bearer_token)

    def test_credential_create_request_rejects_legacy_user_id_field(self):
        with self.assertRaises(ValidationError):
            CredentialCreateRequest(bearer_token="token", user_id="legacy-user")

    async def test_create_admin_credential_returns_new_row_when_numeric_filename_has_gap(self):
        manager = self._manager()
        self.assertTrue(self._add_credential(manager, "first-token", "first-user", "codebuddy_token_1.json"))
        self.assertTrue(self._add_credential(manager, "second-token", "second-user", "codebuddy_token_2.json"))
        self.assertTrue(manager.delete_credential_by_id(manager.get_credentials_info()[0]["credential_id"]))

        bearer_token = jwt_with_payload({"sub": "third-user"})
        with mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager):
            result = await create_admin_credential(
                CredentialCreateRequest(bearer_token=bearer_token),
                self.user,
            )
            listed = await list_admin_credentials(self.user)

        self.assertEqual(result["credential"]["user_id"], "third-user")
        self.assertEqual(result["credential"]["filename"], "codebuddy_token_3.json")
        self.assertNotIn("bearer_token", result["credential"])
        credentials = {item["filename"]: item for item in listed["credentials"]}
        self.assertIn("codebuddy_token_2.json", credentials)
        self.assertIn("codebuddy_token_3.json", credentials)

    async def test_admin_status_leaves_usage_analytics_to_stats_api(self):
        result = await get_admin_status(make_request(path="/api/admin/status"), self.user)

        self.assertNotIn("usage", result)

    async def test_admin_credential_test_uses_selected_row_credential_and_models(self):
        manager = self._manager()
        self.assertTrue(self._add_credential(manager, "current-token", "current-user", "current"))
        self.assertTrue(self._add_credential(
            manager,
            "target-token",
            "target-user",
            "target",
            enterprise_id="enterprise-1",
        ))
        credential_id = manager.get_credentials_info()[1]["credential_id"]
        captured = {}
        stats_context = mock.Mock()

        class FakeService:
            def __init__(self, observer=None):
                captured["observer"] = observer

            async def handle_non_stream_response(self, payload, headers, *, response_model):
                captured["payload"] = payload
                captured["headers"] = headers
                captured["response_model"] = response_model
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
                stats_context=stats_context,
                request_bytes=42,
            )

        self.assertTrue(result["ok"])
        first_model_for_credential.assert_awaited_once()
        called_user, called_credential_id, called_credential = first_model_for_credential.await_args.args
        self.assertEqual(called_user, self.user)
        self.assertEqual(called_credential_id, credential_id)
        self.assertEqual(called_credential["bearer_token"], "target-token")
        first_model.assert_not_awaited()
        self.assertEqual(captured["headers"]["Authorization"], "Bearer target-token")
        self.assertEqual(captured["headers"]["X-Enterprise-Id"], "enterprise-1")
        self.assertEqual(captured["headers"]["X-Tenant-Id"], "enterprise-1")
        self.assertEqual(captured["payload"]["model"], "target-real-model")
        self.assertIs(captured["payload"]["stream"], True)
        self.assertEqual(captured["response_model"], "target-real-model")
        self.assertIs(captured["observer"], stats_context)
        self.assertEqual(stats_context.capture_credential.call_args_list, [
            mock.call(credential_id, credential_id),
            mock.call(credential_id, "target.json"),
        ])
        stats_context.capture_request_bytes.assert_called_once_with(42)
        stats_context.capture_request_shape.assert_called_once()
        self.assertEqual(
            stats_context.capture_request_shape.call_args.args[0]["model"],
            "target-real-model",
        )
        stats_context.capture_prepared_request.assert_called_once_with(captured["payload"])
        stats_context.mark_success.assert_called_once_with()

    async def test_admin_credential_test_returns_ok_false_when_model_lookup_fails(self):
        manager = self._manager()
        self.assertTrue(self._add_credential(manager, "target-token", "target-user", "target"))
        credential_id = manager.get_credentials_info()[0]["credential_id"]
        stats_context = mock.Mock()

        class FakeService:
            async def handle_non_stream_response(self, _payload, _headers, *, response_model):
                raise AssertionError("stream service should not run without a model")

        with (
            mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
            mock.patch(
                "src.admin_router.models_manager.get_first_actual_model_for_credential",
                mock.AsyncMock(side_effect=RuntimeError("敏感的模型查询异常详情")),
            ),
        ):
            result = await test_admin_credential(
                credential_id,
                CredentialTestRequest(message="hello"),
                self.user,
                stream_service_factory=FakeService,
                stats_context=stats_context,
            )

        self.assertIs(result["ok"], False)
        self.assertEqual(result["status_code"], 502)
        self.assertEqual(result["detail"], "无法获取凭证模型")
        self.assertNotIn("敏感的模型查询异常详情", result["detail"])
        self.assertEqual(stats_context.capture_credential.call_args_list, [
            mock.call(credential_id, credential_id),
            mock.call(credential_id, "target.json"),
        ])
        stats_context.mark_failure.assert_called_once_with("model_lookup", 502)

    async def test_missing_admin_credential_returns_404(self):
        with self.assertRaises(HTTPException) as context:
            await delete_admin_credential("missing", self.user)

        self.assertEqual(context.exception.status_code, 404)

        with self.assertRaises(HTTPException) as select_context:
            await select_admin_credential("missing", self.user)
        self.assertEqual(select_context.exception.status_code, 404)

        with self.assertRaises(HTTPException) as test_context:
            await test_admin_credential(
                "missing",
                CredentialTestRequest(),
                self.user,
                stream_service_factory=mock.Mock,
            )
        self.assertEqual(test_context.exception.status_code, 404)

        stats_context = mock.Mock()
        with self.assertRaises(HTTPException) as stats_test_context:
            await test_admin_credential(
                "missing",
                CredentialTestRequest(),
                self.user,
                stream_service_factory=mock.Mock,
                stats_context=stats_context,
            )
        self.assertEqual(stats_test_context.exception.status_code, 404)
        stats_context.capture_request_bytes.assert_called_once_with(0)
        stats_context.capture_credential.assert_called_once_with("missing", "missing")
        stats_context.mark_failure.assert_called_once_with("credential_not_found", 404)

    async def test_toggle_admin_auto_rotation_returns_new_state(self):
        first = await toggle_admin_auto_rotation(self.user)
        second = await toggle_admin_auto_rotation(self.user)

        self.assertIs(first["auto_rotation_enabled"], not second["auto_rotation_enabled"])
        self.assertEqual(
            "启用" in first["message"],
            first["auto_rotation_enabled"],
        )
        self.assertEqual(
            "启用" in second["message"],
            second["auto_rotation_enabled"],
        )

    async def test_admin_credential_test_maps_http_and_unexpected_service_errors(self):
        manager = self._manager()
        self.assertTrue(self._add_credential(manager, "target-token", "target-user", "target"))
        credential_id = manager.get_credentials_info()[0]["credential_id"]

        errors = [
            (HTTPException(status_code=401, detail="unauthorized"), 401, "unauthorized", None),
            (
                RuntimeError("敏感的凭证测试异常详情"),
                500,
                "凭证测试失败",
                "敏感的凭证测试异常详情",
            ),
        ]
        for error, expected_status, expected_detail, sensitive_detail in errors:
            with self.subTest(error=error):
                service = mock.Mock()
                service.handle_non_stream_response = mock.AsyncMock(side_effect=error)
                with (
                    mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
                    mock.patch(
                        "src.admin_router.models_manager.get_first_actual_model_for_credential",
                        new=mock.AsyncMock(return_value="model"),
                    ),
                ):
                    result = await test_admin_credential(
                        credential_id,
                        CredentialTestRequest(message=""),
                        self.user,
                        stream_service_factory=lambda: service,
                    )

                self.assertFalse(result["ok"])
                self.assertEqual(result["status_code"], expected_status)
                self.assertEqual(result["detail"], expected_detail)
                if sensitive_detail:
                    self.assertNotIn(sensitive_detail, result["detail"])

    async def test_admin_credential_test_optional_stats_branches(self):
        manager = self._manager()
        self.assertTrue(self._add_credential(manager, "target-token", "target-user", "target"))
        credential_id = manager.get_credentials_info()[0]["credential_id"]

        class SuccessfulService:
            async def handle_non_stream_response(self, _payload, _headers, *, response_model):
                return {"model": response_model}

        with (
            mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
            mock.patch(
                "src.admin_router.models_manager.get_first_actual_model_for_credential",
                new=mock.AsyncMock(return_value="model"),
            ),
        ):
            result = await test_admin_credential(
                credential_id,
                CredentialTestRequest(),
                self.user,
                stream_service_factory=SuccessfulService,
            )

        self.assertEqual(result, {"ok": True, "status_code": 200})

        with (
            mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
            mock.patch(
                "src.admin_router.models_manager.get_first_actual_model_for_credential",
                new=mock.AsyncMock(side_effect=RuntimeError("models unavailable")),
            ),
        ):
            result = await test_admin_credential(
                credential_id,
                CredentialTestRequest(),
                self.user,
                stream_service_factory=mock.Mock,
            )

        self.assertEqual(result["status_code"], 502)

    async def test_admin_credential_test_records_controlled_error_inputs_for_context(self):
        manager = self._manager()
        self.assertTrue(self._add_credential(manager, "target-token", "target-user", "target"))
        credential_id = manager.get_credentials_info()[0]["credential_id"]
        typed_error = HTTPException(status_code=429, detail="limited")
        typed_error.error = {"type": "quota_error"}
        cases = [
            (typed_error, "quota_error", 429),
            (HTTPException(status_code=401, detail="unauthorized"), "upstream_error", 401),
            (RuntimeError("broken service"), "internal_error", 500),
        ]

        for error, expected_type, expected_status in cases:
            with self.subTest(error=error):
                service = mock.Mock()
                service.handle_non_stream_response = mock.AsyncMock(side_effect=error)
                stats_context = mock.Mock()
                with (
                    mock.patch("src.admin_router.get_token_manager_for_user", return_value=manager),
                    mock.patch(
                        "src.admin_router.models_manager.get_first_actual_model_for_credential",
                        new=mock.AsyncMock(return_value="model"),
                    ),
                ):
                    result = await test_admin_credential(
                        credential_id,
                        CredentialTestRequest(),
                        self.user,
                        stream_service_factory=lambda **_kwargs: service,
                        stats_context=stats_context,
                    )

                self.assertEqual(result["status_code"], expected_status)
                stats_context.mark_failure.assert_called_once_with(expected_type, expected_status)

    async def test_admin_credential_test_route_creates_management_stats_context(self):
        request = mock.Mock()
        request.body = mock.AsyncMock(return_value=b'{"message":"test"}')
        stats_context = mock.Mock()
        factory = mock.Mock()
        request_body = CredentialTestRequest()
        with mock.patch(
                "src.admin_router.test_admin_credential",
                new=mock.AsyncMock(return_value={"ok": True}),
            ) as run_test:
            result = await test_admin_credential_route(
                "credential-1",
                request_body,
                request,
                self.user,
                factory,
                stats_context,
            )

        self.assertEqual(result, {"ok": True})
        run_test.assert_awaited_once_with(
            "credential-1",
            request_body,
            self.user,
            factory,
            stats_context,
            len(b'{"message":"test"}'),
        )

    async def test_credential_test_stats_dependency_attaches_after_authentication(self):
        request = mock.Mock()
        request.body = mock.AsyncMock(return_value=b'{"message":"test"}')
        stats_context = mock.Mock()
        with mock.patch(
            "src.admin_router.create_usage_stats_context",
            return_value=stats_context,
        ) as create_context:
            result = await get_credential_test_stats_context(
                "credential-1",
                request,
                self.user,
            )

        self.assertIs(result, stats_context)
        create_context.assert_called_once_with(request, self.user, "credential_test")
        stats_context.capture_request_bytes.assert_called_once_with(
            len(b'{"message":"test"}')
        )
        stats_context.capture_credential.assert_called_once_with(
            "credential-1",
            "credential-1",
        )

    async def test_settings_contract_returns_typed_fields_and_saves_hot_reloadable_values(self):
        settings = await get_admin_settings(self.user)

        field_by_key = {field["key"]: field for field in settings["fields"]}
        self.assertNotIn("CODEBUDDY_LOG_LEVEL", field_by_key)
        self.assertEqual(field_by_key["CODEBUDDY_AUTO_ROTATION_ENABLED"]["type"], "boolean")
        self.assertEqual(field_by_key["CODEBUDDY_AUTO_ROTATION_ENABLED"]["label"], "凭证轮换")
        self.assertIn("有效凭证", field_by_key["CODEBUDDY_AUTO_ROTATION_ENABLED"]["description"])
        self.assertEqual(field_by_key["CODEBUDDY_STRIP_MODEL_NAMESPACE"]["type"], "boolean")
        self.assertIn("provider/model", field_by_key["CODEBUDDY_STRIP_MODEL_NAMESPACE"]["description"])
        self.assertIn("reasoning_effort=max", field_by_key["CODEBUDDY_FORCED_REASONING_MODELS"]["description"])
        self.assertIn("temperature", field_by_key["CODEBUDDY_FORCED_TEMPERATURE"]["description"])
        self.assertIn("CodeBuddy 配置接口", field_by_key["CODEBUDDY_MODELS"]["description"])
        self.assertEqual(field_by_key["CODEBUDDY_ROTATION_COUNT"]["type"], "number")
        self.assertEqual(field_by_key["CODEBUDDY_ROTATION_COUNT"]["min"], 1)
        self.assertIn("正整数", field_by_key["CODEBUDDY_ROTATION_COUNT"]["description"])

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
        result_field_by_key = {field["key"]: field for field in result["fields"]}
        self.assertEqual(
            result_field_by_key["CODEBUDDY_MODELS"]["description"],
            field_by_key["CODEBUDDY_MODELS"]["description"],
        )
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


class AdminHelperTests(unittest.TestCase):
    def test_time_remaining_text_formats_all_ranges(self):
        cases = [
            (None, "Unknown"),
            (0, "Expired"),
            (-1, "Expired"),
            (30, "0m"),
            (3660, "1h 1m"),
            (90000, "1d 1h"),
        ]

        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(_time_remaining_text(seconds), expected)

    def test_safe_credential_hides_index_and_token(self):
        info = {"index": 0, "credential_id": "id", "time_remaining": 60}

        with_token = _safe_credential(info, {"bearer_token": "1234567890abcdef"})
        exact_visible_length_token = _safe_credential(info, {"bearer_token": "1234567890abcd"})
        short_token = _safe_credential(info, {"bearer_token": "short"})
        without_token = _safe_credential(info, None)

        self.assertNotIn("index", with_token)
        self.assertNotIn("token_preview", with_token)
        self.assertEqual(with_token["token_display"], "123456...90abcdef")
        self.assertTrue(with_token["has_token"])
        self.assertEqual(exact_visible_length_token["token_display"], "********")
        self.assertTrue(exact_visible_length_token["has_token"])
        self.assertEqual(short_token["token_display"], "********")
        self.assertTrue(short_token["has_token"])
        self.assertEqual(without_token["token_display"], "")
        self.assertFalse(without_token["has_token"])

    def test_safe_credentials_handles_missing_and_out_of_range_indexes(self):
        manager = mock.Mock()
        manager.get_all_credentials.return_value = [{"bearer_token": "token"}]
        manager.get_credentials_info.return_value = [
            {"credential_id": "missing", "time_remaining": None},
            {"credential_id": "out", "index": 2, "time_remaining": None},
        ]

        credentials = _safe_credentials(manager)

        self.assertEqual([item["has_token"] for item in credentials], [False, False])

    def test_stream_service_factory_returns_service_class(self):
        from src.stream_service import CodeBuddyStreamService

        self.assertIs(get_stream_service_factory(), CodeBuddyStreamService)


if __name__ == "__main__":
    unittest.main()
