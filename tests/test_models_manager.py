import unittest
from unittest import mock

import config

from src.auth_types import AuthenticatedUser
from src.models_manager import ModelsManager

from tests.helpers import ConfigIsolationMixin


class FakeTokenManager:
    def __init__(self, credential=None):
        self.credential = credential

    def get_next_credential(self):
        return self.credential


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
        return self.response


class ModelsManagerTests(ConfigIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _make_manager(self, client):
        return ModelsManager(http_client_factory=lambda **_kwargs: client)

    def _user(self, username="admin"):
        return AuthenticatedUser(username=username, source="users_file")

    async def test_merges_configured_models_first_with_actual_models(self):
        config._config_cache["CODEBUDDY_MODELS"] = "manual-a, shared, ,manual-b"
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

    async def test_falls_back_to_configured_and_cached_models_when_upstream_fails(self):
        config._config_cache["CODEBUDDY_MODELS"] = "manual-a, shared"
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))
        manager._models_cache["admin"] = ["shared", "cached-b"]

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            models = await manager.get_available_models(self._user())

        self.assertEqual(models, ["manual-a", "shared", "cached-b"])

    async def test_falls_back_to_configured_models_when_no_cached_models_exist(self):
        config._config_cache["CODEBUDDY_MODELS"] = "manual-a,"
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value"}),
        ):
            models = await manager.get_available_models(self._user())

        self.assertEqual(models, ["manual-a"])

    async def test_cache_isolated_by_authenticated_username(self):
        config._config_cache["CODEBUDDY_MODELS"] = "manual-a"
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))
        manager._models_cache["other-user"] = ["other-only"]

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager(None),
        ):
            models = await manager.get_available_models(self._user("admin"))

        self.assertEqual(models, ["manual-a"])


if __name__ == "__main__":
    unittest.main()
