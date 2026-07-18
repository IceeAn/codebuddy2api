import unittest
from unittest import mock

from src.auth_types import AuthenticatedUser
from src.chat_execution import (
    CodeBuddyCredentialError,
    execute_codebuddy_chat,
    select_codebuddy_credential,
    select_codebuddy_credential_with_id,
)
from src.request_processor import PreparedCodeBuddyRequest


class ChatExecutionTests(unittest.IsolatedAsyncioTestCase):
    def test_default_credential_selector(self):
        manager = mock.Mock()
        manager.get_next_credential.return_value = {"bearer_token": "token"}
        self.assertEqual(select_codebuddy_credential(manager)["bearer_token"], "token")

        for value in (None, [], {}, {"user_id": "missing"}):
            manager.get_next_credential.return_value = value
            with self.subTest(value=value), self.assertRaises(CodeBuddyCredentialError):
                select_codebuddy_credential(manager)

        manager.get_next_credential.side_effect = RuntimeError("store")
        with self.assertRaises(CodeBuddyCredentialError):
            select_codebuddy_credential(manager)

    def test_credential_selector_with_id_validates_native_and_legacy_managers(self):
        class NativeManager:
            def __init__(self, result=None, error=None):
                self.result = result
                self.error = error

            def select_next_credential(self):
                if self.error:
                    raise self.error
                return self.result

        selected = ("stable-id", {"bearer_token": "token"})
        self.assertEqual(select_codebuddy_credential_with_id(NativeManager(selected)), selected)
        for manager in (
            NativeManager(None),
            NativeManager(error=RuntimeError("store")),
        ):
            with self.subTest(manager=manager), self.assertRaises(CodeBuddyCredentialError):
                select_codebuddy_credential_with_id(manager)

        legacy = mock.Mock()
        legacy.select_next_credential.return_value = mock.Mock()
        legacy.get_next_credential.return_value = {"bearer_token": "legacy-token"}
        legacy.get_current_credential_info.return_value = None
        self.assertEqual(
            select_codebuddy_credential_with_id(legacy),
            (None, {"bearer_token": "legacy-token"}),
        )

        class LegacyManager:
            def get_next_credential(self):
                return {"bearer_token": "legacy-token"}

            def get_current_credential_info(self):
                return {"credential_id": "legacy-id"}

        self.assertEqual(
            select_codebuddy_credential_with_id(LegacyManager()),
            ("legacy-id", {"bearer_token": "legacy-token"}),
        )

    async def test_stream_execution_without_stats_or_adapter_uses_empty_optional_headers(self):
        user = AuthenticatedUser(username="alice", source="api_key")
        manager = mock.Mock()
        service = mock.Mock()
        service.handle_stream_response = mock.AsyncMock(return_value="stream")
        header_generator = mock.Mock(return_value={"Authorization": "Bearer upstream"})
        prepared = PreparedCodeBuddyRequest(
            payload={"model": "glm", "stream": True},
            client_wants_stream=True,
            response_model="glm",
        )

        result = await execute_codebuddy_chat(
            prepared,
            user,
            token_manager_factory=lambda _user: manager,
            credential_selector=lambda _manager: {"bearer_token": "token"},
            header_generator=header_generator,
            service_factory=lambda **_kwargs: service,
        )

        self.assertEqual(result, "stream")
        header_generator.assert_called_once_with(
            bearer_token="token",
            user_id=None,
            account_uid=None,
            domain=None,
            enterprise_id=None,
            department_full_name=None,
            conversation_id=None,
            conversation_request_id=None,
            conversation_message_id=None,
            request_id=None,
        )
        service.handle_stream_response.assert_awaited_once_with(
            prepared.payload,
            {"Authorization": "Bearer upstream"},
            response_model="glm",
        )

    async def test_non_stream_execution_passes_response_adapter(self):
        user = AuthenticatedUser(username="alice", source="api_key")
        manager = mock.Mock()
        service = mock.Mock()
        service.handle_non_stream_response = mock.AsyncMock(return_value={"ok": True})
        prepared = PreparedCodeBuddyRequest(
            payload={"model": "glm", "stream": True},
            client_wants_stream=False,
            response_model="client-model",
        )
        adapter = object()

        result = await execute_codebuddy_chat(
            prepared,
            user,
            response_adapter=adapter,
            token_manager_factory=lambda _user: manager,
            credential_selector=lambda _manager: {"bearer_token": "token"},
            header_generator=lambda **_kwargs: {},
            service_factory=lambda **_kwargs: service,
        )

        self.assertEqual(result, {"ok": True})
        service.handle_non_stream_response.assert_awaited_once_with(
            prepared.payload,
            {},
            response_model="client-model",
            response_adapter=adapter,
        )

    async def test_default_execution_captures_id_returned_with_selected_credential(self):
        user = AuthenticatedUser(username="alice", source="api_key")
        manager = mock.Mock()
        manager.select_next_credential.return_value = (
            "selected-id",
            {"bearer_token": "token", "user_id": "user"},
        )
        manager.get_credential_info_by_id.return_value = {"credential_id": "selected-id"}
        service = mock.Mock()
        service.handle_non_stream_response = mock.AsyncMock(return_value={"ok": True})
        stats_context = mock.Mock()
        prepared = PreparedCodeBuddyRequest(
            payload={"model": "glm", "stream": True},
            client_wants_stream=False,
            response_model="glm",
        )

        result = await execute_codebuddy_chat(
            prepared,
            user,
            stats_context=stats_context,
            token_manager_factory=lambda _user: manager,
            header_generator=lambda **_kwargs: {},
            service_factory=lambda **_kwargs: service,
        )

        self.assertEqual(result, {"ok": True})
        stats_context.capture_credential.assert_called_once_with("selected-id", "selected-id")
        manager.get_current_credential_info.assert_not_called()

    async def test_execution_falls_back_to_matching_current_info(self):
        user = AuthenticatedUser(username="alice", source="api_key")
        manager = mock.Mock()
        manager.get_credential_info_by_id.return_value = None
        manager.get_current_credential_info.return_value = {
            "credential_id": "selected-id",
            "filename": "selected.json",
        }
        service = mock.Mock()
        service.handle_non_stream_response = mock.AsyncMock(return_value={"ok": True})
        stats_context = mock.Mock()
        prepared = PreparedCodeBuddyRequest(
            payload={"model": "glm", "stream": True},
            client_wants_stream=False,
            response_model="glm",
        )

        await execute_codebuddy_chat(
            prepared,
            user,
            stats_context=stats_context,
            token_manager_factory=lambda _user: manager,
            credential_selector=lambda _manager: ("selected-id", {"bearer_token": "token"}),
            header_generator=lambda **_kwargs: {},
            service_factory=lambda **_kwargs: service,
        )

        stats_context.capture_credential.assert_called_once_with("selected-id", "selected.json")

        manager.get_current_credential_info.return_value = {
            "credential_id": "different-id",
            "filename": "different.json",
        }
        different_stats = mock.Mock()
        await execute_codebuddy_chat(
            prepared,
            user,
            stats_context=different_stats,
            token_manager_factory=lambda _user: manager,
            credential_selector=lambda _manager: ("selected-id", {"bearer_token": "token"}),
            header_generator=lambda **_kwargs: {},
            service_factory=lambda **_kwargs: service,
        )
        different_stats.capture_credential.assert_called_once_with("selected-id", "selected-id")
