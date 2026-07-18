import unittest
from unittest import mock

from fastapi import HTTPException

from src.auth_types import AuthenticatedUser
from src.openai_router import (
    CredentialManager,
    chat_completions,
    get_available_models_list,
    list_v1_models,
)
from src.request_processor import PreparedCodeBuddyRequest


class FakeChatRequest:
    def __init__(self, body=None, error=None):
        self.body = body
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.body


class CredentialManagerTests(unittest.TestCase):
    def test_returns_complete_credential(self):
        manager = mock.Mock()
        manager.get_next_credential.return_value = {
            "bearer_token": "token",
            "user_id": "user",
        }

        credential = CredentialManager.get_valid_credential(manager)

        self.assertEqual(credential["bearer_token"], "token")

    def test_rejects_missing_or_invalid_credential(self):
        for credential in (None, {}, {"user_id": "user"}):
            with self.subTest(credential=credential):
                manager = mock.Mock()
                manager.get_next_credential.return_value = credential

                with self.assertRaises(HTTPException) as raised:
                    CredentialManager.get_valid_credential(manager)

                self.assertEqual(raised.exception.status_code, 401)
                self.assertEqual(raised.exception.detail, "凭证获取失败")

    def test_maps_token_manager_error_to_unauthorized(self):
        manager = mock.Mock()
        manager.get_next_credential.side_effect = RuntimeError("broken store")

        with self.assertRaises(HTTPException) as raised:
            CredentialManager.get_valid_credential(manager)

        self.assertEqual(raised.exception.status_code, 401)

    def test_atomic_credential_selection_validates_result(self):
        class Manager:
            def __init__(self, selected):
                self.selected = selected

            def select_next_credential(self):
                return self.selected

        selected = ("credential-id", {"bearer_token": "token"})
        self.assertEqual(CredentialManager.get_valid_credential_selection(Manager(selected)), selected)

        for value in (None, ("credential-id", {})):
            with self.subTest(value=value), self.assertRaises(HTTPException) as raised:
                CredentialManager.get_valid_credential_selection(Manager(value))
            self.assertEqual(raised.exception.status_code, 401)


class OpenAIRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = AuthenticatedUser(username="alice", source="api_key")
        self.credential = {
            "bearer_token": "token",
            "user_id": "upstream-user",
            "domain": "codebuddy.example",
            "enterprise_id": "enterprise-1",
        }

    async def test_get_available_models_delegates_with_user(self):
        with mock.patch(
            "src.openai_router.models_manager.get_available_models",
            new=mock.AsyncMock(return_value=["model-a"]),
        ) as get_models:
            result = await get_available_models_list(self.user)

        self.assertEqual(result, ["model-a"])
        get_models.assert_awaited_once_with(self.user)

    async def test_chat_completion_forwards_stream_request_and_conversation_headers(self):
        request_body = {
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        }
        token_manager = mock.Mock()
        token_manager.get_next_credential.return_value = self.credential
        token_manager.get_current_credential_info.return_value = {
            "credential_id": "credential-1",
            "filename": "credential.json",
        }
        service = mock.Mock()
        service.handle_stream_response = mock.AsyncMock(return_value="stream-response")
        service.handle_non_stream_response = mock.AsyncMock()
        stats_context = mock.Mock()

        with (
            mock.patch("src.openai_router.get_token_manager_for_user", return_value=token_manager),
            mock.patch(
                "src.openai_router.codebuddy_api_client.generate_codebuddy_headers",
                return_value={"Authorization": "Bearer token"},
            ) as generate_headers,
            mock.patch(
                "src.openai_router.RequestProcessor.prepare_request",
                return_value=PreparedCodeBuddyRequest(
                    payload={"model": "glm-5.2", "stream": True},
                    client_wants_stream=True,
                    response_model="glm-5.2",
                ),
            ) as prepare_request,
            mock.patch("src.openai_router.CodeBuddyStreamService", return_value=service) as service_class,
        ):
            result = await chat_completions(
                FakeChatRequest(request_body),
                x_conversation_id="conversation",
                x_conversation_request_id="conversation-request",
                x_conversation_message_id="message",
                x_request_id="request",
                _user=self.user,
                stats_context=stats_context,
                request_bytes=123,
            )

        self.assertEqual(result, "stream-response")
        generate_headers.assert_called_once_with(
            bearer_token="token",
            user_id="upstream-user",
            account_uid=None,
            domain="codebuddy.example",
            enterprise_id="enterprise-1",
            department_full_name=None,
            conversation_id="conversation",
            conversation_request_id="conversation-request",
            conversation_message_id="message",
            request_id="request",
        )
        prepare_request.assert_called_once_with(request_body, self.user)
        stats_context.capture_credential.assert_called_once_with(
            "credential-1", "credential.json"
        )
        stats_context.capture_request_bytes.assert_called_once_with(123)
        stats_context.capture_request_shape.assert_called_once_with(request_body)
        stats_context.capture_prepared_request.assert_called_once_with(
            {"model": "glm-5.2", "stream": True}
        )
        service_class.assert_called_once_with(observer=stats_context)
        service.handle_stream_response.assert_awaited_once_with(
            {"model": "glm-5.2", "stream": True},
            {"Authorization": "Bearer token"},
            response_model="glm-5.2",
        )
        service.handle_non_stream_response.assert_not_awaited()

    async def test_chat_completion_forwards_non_stream_request(self):
        request_body = {
            "model": "glm-5.2",
            "messages": [{"role": "user", "content": "hello"}],
        }
        token_manager = mock.Mock()
        token_manager.get_next_credential.return_value = self.credential
        token_manager.get_current_credential_info.return_value = {
            "credential_id": "credential-2",
            "user_id": "fallback-label",
        }
        service = mock.Mock()
        service.handle_stream_response = mock.AsyncMock()
        service.handle_non_stream_response = mock.AsyncMock(return_value={"ok": True})
        stats_context = mock.Mock()

        with (
            mock.patch("src.openai_router.get_token_manager_for_user", return_value=token_manager),
            mock.patch(
                "src.openai_router.codebuddy_api_client.generate_codebuddy_headers",
                return_value={"Authorization": "Bearer token"},
            ),
            mock.patch(
                "src.openai_router.RequestProcessor.prepare_request",
                return_value=PreparedCodeBuddyRequest(
                    payload={"model": "resolved-model", "stream": True},
                    client_wants_stream=False,
                    response_model="codebuddy/resolved-model",
                ),
            ),
            mock.patch("src.openai_router.CodeBuddyStreamService", return_value=service) as service_class,
        ):
            result = await chat_completions(
                FakeChatRequest(request_body),
                _user=self.user,
                stats_context=stats_context,
                request_bytes=88,
            )

        self.assertEqual(result, {"ok": True})
        stats_context.capture_credential.assert_called_once_with(
            "credential-2", "fallback-label"
        )
        stats_context.capture_request_bytes.assert_called_once_with(88)
        stats_context.capture_request_shape.assert_called_once_with(request_body)
        stats_context.capture_prepared_request.assert_called_once_with(
            {"model": "resolved-model", "stream": True}
        )
        service_class.assert_called_once_with(observer=stats_context)
        service.handle_non_stream_response.assert_awaited_once_with(
            {"model": "resolved-model", "stream": True},
            {"Authorization": "Bearer token"},
            response_model="codebuddy/resolved-model",
        )
        service.handle_stream_response.assert_not_awaited()

    async def test_chat_completion_attributes_usage_to_atomically_selected_credential(self):
        request_body = {
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
        }

        class AtomicManager:
            def select_next_credential(self):
                return "selected-id", {
                    **self_credential,
                    "user_id": "selected-user",
                }, 7

            def get_credential_info_by_id(self, credential_id):
                self.requested_credential_id = credential_id
                return {
                    "credential_id": credential_id,
                    "filename": "selected.json",
                }

            def get_current_credential_info(self):
                raise AssertionError("原子选择后不应重新读取当前凭证")

        self_credential = self.credential
        token_manager = AtomicManager()
        service = mock.Mock()
        service.handle_non_stream_response = mock.AsyncMock(return_value={"ok": True})
        stats_context = mock.Mock()

        with (
            mock.patch("src.openai_router.get_token_manager_for_user", return_value=token_manager),
            mock.patch(
                "src.openai_router.codebuddy_api_client.generate_codebuddy_headers",
                return_value={},
            ),
            mock.patch(
                "src.openai_router.RequestProcessor.prepare_request",
                return_value=PreparedCodeBuddyRequest(
                    payload={"model": "model", "stream": True},
                    client_wants_stream=False,
                    response_model="model",
                ),
            ),
            mock.patch("src.openai_router.CodeBuddyStreamService", return_value=service),
        ):
            result = await chat_completions(
                FakeChatRequest(request_body),
                _user=self.user,
                stats_context=stats_context,
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(token_manager.requested_credential_id, "selected-id")
        stats_context.capture_credential.assert_called_once_with(
            "selected-id",
            "selected.json",
            generation=7,
        )

    async def test_chat_completion_rejects_invalid_json(self):
        stats_context = mock.Mock()
        with self.assertRaises(HTTPException) as raised:
            await chat_completions(
                FakeChatRequest(error=ValueError("bad json")),
                _user=self.user,
                stats_context=stats_context,
                request_bytes=13,
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("bad json", raised.exception.detail)
        stats_context.capture_request_bytes.assert_called_once_with(13)
        stats_context.capture_request_shape.assert_not_called()
        stats_context.mark_failure.assert_called_once_with("validation_error", 400)

        with self.assertRaises(HTTPException) as without_stats:
            await chat_completions(
                FakeChatRequest(error=ValueError("still bad")),
                _user=self.user,
            )
        self.assertEqual(without_stats.exception.status_code, 400)

    async def test_chat_completion_preserves_http_exception(self):
        stats_context = mock.Mock()
        with mock.patch(
            "src.openai_router.RequestProcessor.validate_request",
            side_effect=HTTPException(status_code=422, detail="invalid request"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await chat_completions(
                    FakeChatRequest({}),
                    _user=self.user,
                    stats_context=stats_context,
                )

        self.assertEqual(raised.exception.status_code, 422)
        stats_context.capture_request_bytes.assert_called_once_with(0)
        stats_context.capture_request_shape.assert_called_once_with({})
        stats_context.capture_prepared_request.assert_not_called()
        stats_context.mark_failure.assert_called_once_with("validation_error", 422)

        with mock.patch(
            "src.openai_router.RequestProcessor.validate_request",
            side_effect=HTTPException(status_code=422, detail="invalid request"),
        ):
            with self.assertRaises(HTTPException) as without_stats:
                await chat_completions(FakeChatRequest({}), _user=self.user)
        self.assertEqual(without_stats.exception.status_code, 422)

    async def test_chat_completion_records_missing_credential(self):
        token_manager = mock.Mock()
        token_manager.get_next_credential.return_value = None
        stats_context = mock.Mock()

        with mock.patch(
            "src.openai_router.get_token_manager_for_user",
            return_value=token_manager,
        ):
            with self.assertRaises(HTTPException) as raised:
                await chat_completions(
                    FakeChatRequest({
                        "model": "model",
                        "messages": [{"role": "user", "content": "hello"}],
                    }),
                    _user=self.user,
                    stats_context=stats_context,
                )

        self.assertEqual(raised.exception.status_code, 401)
        stats_context.capture_request_bytes.assert_called_once_with(0)
        stats_context.capture_request_shape.assert_called_once_with({
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
        })
        stats_context.capture_prepared_request.assert_not_called()
        stats_context.mark_failure.assert_called_once_with("no_credential", 401)

        with mock.patch(
            "src.openai_router.get_token_manager_for_user",
            return_value=token_manager,
        ):
            with self.assertRaises(HTTPException) as without_stats:
                await chat_completions(
                    FakeChatRequest({
                        "model": "model",
                        "messages": [{"role": "user", "content": "hello"}],
                    }),
                    _user=self.user,
                )
        self.assertEqual(without_stats.exception.status_code, 401)

    async def test_chat_completion_succeeds_without_optional_stats_context(self):
        request_body = {
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
        }
        token_manager = mock.Mock()
        token_manager.get_next_credential.return_value = self.credential
        service = mock.Mock()
        service.handle_non_stream_response = mock.AsyncMock(return_value={"ok": True})

        with (
            mock.patch("src.openai_router.get_token_manager_for_user", return_value=token_manager),
            mock.patch(
                "src.openai_router.codebuddy_api_client.generate_codebuddy_headers",
                return_value={"Authorization": "Bearer token"},
            ),
            mock.patch(
                "src.openai_router.RequestProcessor.prepare_request",
                return_value=PreparedCodeBuddyRequest(
                    payload={"model": "model", "stream": True},
                    client_wants_stream=False,
                    response_model="model",
                ),
            ),
            mock.patch("src.openai_router.CodeBuddyStreamService", return_value=service) as service_class,
        ):
            result = await chat_completions(FakeChatRequest(request_body), _user=self.user)

        self.assertEqual(result, {"ok": True})
        token_manager.get_current_credential_info.assert_not_called()
        service_class.assert_called_once_with(observer=None)

    async def test_chat_completion_maps_unexpected_error(self):
        stats_context = mock.Mock()
        with mock.patch(
            "src.openai_router.RequestProcessor.validate_request",
            side_effect=RuntimeError("unexpected"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await chat_completions(
                    FakeChatRequest({}),
                    _user=self.user,
                    stats_context=stats_context,
                )

        self.assertEqual(raised.exception.status_code, 500)
        self.assertIn("unexpected", raised.exception.detail)
        stats_context.capture_request_bytes.assert_called_once_with(0)
        stats_context.capture_request_shape.assert_called_once_with({})
        stats_context.mark_failure.assert_called_once_with("internal_error", 500)

        with mock.patch(
            "src.openai_router.RequestProcessor.validate_request",
            side_effect=RuntimeError("unexpected without stats"),
        ):
            with self.assertRaises(HTTPException) as without_stats:
                await chat_completions(FakeChatRequest({}), _user=self.user)
        self.assertEqual(without_stats.exception.status_code, 500)

    async def test_chat_completion_uses_credential_id_label_and_zero_request_bytes(self):
        request_body = {
            "model": "model",
            "messages": [{"role": "user", "content": "hello"}],
        }
        class LegacyAtomicManager:
            def select_next_credential(self):
                return "credential-only-label", self_credential

            def get_credential_info_by_id(self, _credential_id):
                return {"credential_id": "credential-only-label"}

        self_credential = self.credential
        token_manager = LegacyAtomicManager()
        service = mock.Mock()
        service.handle_non_stream_response = mock.AsyncMock(return_value={"ok": True})
        stats_context = mock.Mock()

        with (
            mock.patch("src.openai_router.get_token_manager_for_user", return_value=token_manager),
            mock.patch(
                "src.openai_router.codebuddy_api_client.generate_codebuddy_headers",
                return_value={},
            ),
            mock.patch(
                "src.openai_router.RequestProcessor.prepare_request",
                return_value=PreparedCodeBuddyRequest(
                    payload={"model": "model", "stream": True},
                    client_wants_stream=False,
                    response_model="model",
                ),
            ),
            mock.patch("src.openai_router.CodeBuddyStreamService", return_value=service),
        ):
            await chat_completions(
                FakeChatRequest(request_body),
                _user=self.user,
                stats_context=stats_context,
            )

        stats_context.capture_credential.assert_called_once_with(
            "credential-only-label",
            "credential-only-label",
        )
        stats_context.capture_request_bytes.assert_called_once_with(0)
        stats_context.capture_request_shape.assert_called_once_with(request_body)
        stats_context.capture_prepared_request.assert_called_once_with(
            {"model": "model", "stream": True}
        )

    async def test_model_list_maps_manager_error(self):
        with mock.patch(
            "src.openai_router.get_available_models_list",
            new=mock.AsyncMock(side_effect=RuntimeError("upstream failed")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await list_v1_models(self.user)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "获取模型列表失败")


if __name__ == "__main__":
    unittest.main()
