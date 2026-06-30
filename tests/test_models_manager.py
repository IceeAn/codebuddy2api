import unittest
from unittest import mock

import config

from src.auth_types import AuthenticatedUser
from src.models_manager import ModelsManager

from tests.helpers import ConfigIsolationMixin


class FakeTokenManager:
    def __init__(self, credential=None, credential_id="credential-id"):
        self.credential = credential
        self.credential_id = credential_id

    def get_next_credential(self):
        return self.credential

    def get_current_credential_info(self):
        if not self.credential:
            return {}
        return {"credential_id": self.credential_id}

    def get_credential_by_id(self, credential_id):
        if credential_id != self.credential_id:
            return None
        return self.credential

    @staticmethod
    def is_token_expired(credential):
        return credential.get("expired", False)


class FakeConfigResponse:
    def __init__(self, body, status_code=200, text=""):
        self._body = body
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._body


class FakeConfigClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.requests.append({"url": url, "headers": headers})
        if self.error:
            raise self.error
        if callable(self.response):
            return self.response(headers, len(self.requests))
        return self.response


class ModelsManagerTests(ConfigIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _make_manager(self, client):
        return ModelsManager(http_client_factory=lambda **_kwargs: client)

    def _user(self, username="admin"):
        return AuthenticatedUser(username=username, source="users_file")

    async def test_merges_configured_models_first_with_actual_models(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a, shared, ,manual-b"}, username="admin")
        response = FakeConfigResponse({
            "code": 0,
            "data": {
                "models": [
                    {"id": "shared"},
                    {"id": " real-b "},
                    {"id": ""},
                    {"id": "real-c"},
                ]
            },
        })
        client = FakeConfigClient(response=response)
        manager = self._make_manager(client)

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({
                    "bearer_token": "token-value",
                    "user_id": "user-id",
                    "domain": "copilot.tencent.com",
                }),
        ):
            models = await manager.get_available_models(self._user())

        self.assertEqual(models, ["manual-a", "shared", "manual-b", "real-b", "real-c"])
        request_headers = client.requests[0]["headers"]
        self.assertEqual(request_headers["X-IDE-Type"], "CodeBuddyIDE")
        self.assertEqual(request_headers["X-IDE-Name"], "CodeBuddyIDE")
        self.assertEqual(request_headers["Host"], "copilot.tencent.com")
        self.assertEqual(request_headers["X-Domain"], "copilot.tencent.com")

    async def test_configured_models_are_user_scoped(self):
        config.update_settings({"CODEBUDDY_MODELS": "admin-model"}, username="admin")
        config.update_settings({"CODEBUDDY_MODELS": "alice-model"}, username="alice")
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            admin_models = await manager.get_available_models(self._user("admin"))
            alice_models = await manager.get_available_models(self._user("alice"))

        self.assertEqual(admin_models, ["admin-model"])
        self.assertEqual(alice_models, ["alice-model"])

    async def test_uses_cached_actual_models_within_ttl(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a"}, username="admin")
        response = FakeConfigResponse({
            "code": 0,
            "data": {"models": [{"id": "real-a"}, {"id": "real-b"}]},
        })
        client = FakeConfigClient(response=response)
        now = 1000.0
        manager = ModelsManager(
            http_client_factory=lambda **_kwargs: client,
            monotonic_factory=lambda: now,
        )

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            first = await manager.get_available_models(self._user())
            now += 599
            second = await manager.get_available_models(self._user())

        self.assertEqual(first, ["manual-a", "real-a", "real-b"])
        self.assertEqual(second, ["manual-a", "real-a", "real-b"])
        self.assertEqual(len(client.requests), 1)

    async def test_skips_cached_models_when_current_credential_has_expired(self):
        response = FakeConfigResponse({
            "code": 0,
            "data": {"models": [{"id": "valid-model"}]},
        })
        client = FakeConfigClient(response=response)
        manager = ModelsManager(
            http_client_factory=lambda **_kwargs: client,
            monotonic_factory=lambda: 1000.0,
        )
        manager._models_cache["admin:expired-credential"] = ["expired-model"]
        manager._models_cache_expires_at["admin:expired-credential"] = 1600.0

        expired_credential = {"bearer_token": "expired-token"}
        valid_credential = {"bearer_token": "valid-token"}
        token_manager = mock.Mock()
        token_manager.get_current_credential_info.side_effect = [
            {"credential_id": "expired-credential"},
            {"credential_id": "valid-credential"},
        ]
        token_manager.get_credential_by_id.return_value = expired_credential
        token_manager.is_token_expired.return_value = True
        token_manager.get_next_credential.return_value = valid_credential

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=token_manager,
        ):
            models = await manager.get_actual_models(self._user())

        self.assertEqual(models, ["valid-model"])
        token_manager.is_token_expired.assert_called_once_with(expired_credential)
        token_manager.get_next_credential.assert_called_once_with()
        self.assertEqual(len(client.requests), 1)

    async def test_refreshes_actual_models_after_ttl_expires(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a"}, username="admin")
        response = FakeConfigResponse({
            "code": 0,
            "data": {"models": [{"id": "real-a"}]},
        })
        client = FakeConfigClient(response=response)
        now = 1000.0
        manager = ModelsManager(
            http_client_factory=lambda **_kwargs: client,
            monotonic_factory=lambda: now,
        )

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            await manager.get_available_models(self._user())
            now += 600
            await manager.get_available_models(self._user())

        self.assertEqual(len(client.requests), 2)

    async def test_actual_model_cache_is_isolated_by_credential_id(self):
        def response_for(headers, _request_count):
            if headers["Authorization"] == "Bearer token-a":
                return FakeConfigResponse({"code": 0, "data": {"models": [{"id": "model-a"}]}})
            return FakeConfigResponse({"code": 0, "data": {"models": [{"id": "model-b"}]}})

        client = FakeConfigClient(response=response_for)
        manager = self._make_manager(client)
        user = self._user()

        first = await manager.get_actual_models_for_credential(
            user,
            "credential-a",
            {"bearer_token": "token-a", "user_id": "user-a"},
        )
        second = await manager.get_actual_models_for_credential(
            user,
            "credential-b",
            {"bearer_token": "token-b", "user_id": "user-b"},
        )
        cached_first = await manager.get_actual_models_for_credential(
            user,
            "credential-a",
            {"bearer_token": "token-a", "user_id": "user-a"},
        )

        self.assertEqual(first, ["model-a"])
        self.assertEqual(second, ["model-b"])
        self.assertEqual(cached_first, ["model-a"])
        self.assertEqual(len(client.requests), 2)

    async def test_actual_model_cache_refreshes_per_credential_after_ttl(self):
        def response_for(_headers, request_count):
            model_id = "model-a" if request_count == 1 else "model-a-refreshed"
            return FakeConfigResponse({"code": 0, "data": {"models": [{"id": model_id}]}})

        client = FakeConfigClient(response=response_for)
        now = 1000.0
        manager = ModelsManager(
            http_client_factory=lambda **_kwargs: client,
            monotonic_factory=lambda: now,
        )
        user = self._user()
        credential = {"bearer_token": "token-a", "user_id": "user-a"}

        first = await manager.get_actual_models_for_credential(user, "credential-a", credential)
        now += 599
        cached = await manager.get_actual_models_for_credential(user, "credential-a", credential)
        now += 1
        refreshed = await manager.get_actual_models_for_credential(user, "credential-a", credential)

        self.assertEqual(first, ["model-a"])
        self.assertEqual(cached, ["model-a"])
        self.assertEqual(refreshed, ["model-a-refreshed"])
        self.assertEqual(len(client.requests), 2)

    async def test_get_first_actual_model_returns_first_model_from_config_api(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a"}, username="admin")
        response = FakeConfigResponse({
            "code": 0,
            "data": {"models": [{"id": "real-first"}, {"id": "real-second"}]},
        })
        client = FakeConfigClient(response=response)
        manager = self._make_manager(client)

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            model = await manager.get_first_actual_model(self._user())

        self.assertEqual(model, "real-first")

    async def test_falls_back_to_configured_and_cached_models_when_upstream_fails(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a, shared"}, username="admin")
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))
        manager._models_cache["admin:credential-id"] = ["shared", "cached-b"]

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            models = await manager.get_available_models(self._user())

        self.assertEqual(models, ["manual-a", "shared", "cached-b"])

    async def test_falls_back_to_configured_models_when_no_cached_models_exist(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a,"}, username="admin")
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            models = await manager.get_available_models(self._user())

        self.assertEqual(models, ["manual-a"])

    async def test_cache_isolated_by_authenticated_username(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a"}, username="admin")
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))
        manager._models_cache["other-user:credential-id"] = ["other-only"]

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager(None),
        ):
            models = await manager.get_available_models(self._user("admin"))

        self.assertEqual(models, ["manual-a"])


if __name__ == "__main__":
    unittest.main()
