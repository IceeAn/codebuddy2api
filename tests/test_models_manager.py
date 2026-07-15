import asyncio
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

    def preview_next_credential(self):
        if not self.credential:
            return None
        return self.credential_id, self.credential

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

    async def get(self, url, headers=None, **_kwargs):
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
                    "enterprise_id": "enterprise-1",
                }),
        ):
            models = await manager.get_available_models(self._user())

        self.assertEqual(models, ["manual-a", "shared", "manual-b", "real-b", "real-c"])
        request_headers = client.requests[0]["headers"]
        self.assertEqual(request_headers["X-IDE-Type"], "CodeBuddyIDE")
        self.assertEqual(request_headers["X-IDE-Name"], "CodeBuddyIDE")
        self.assertEqual(request_headers["Host"], "copilot.tencent.com")
        self.assertEqual(request_headers["X-Domain"], "copilot.tencent.com")
        self.assertEqual(request_headers["X-Enterprise-Id"], "enterprise-1")
        self.assertEqual(request_headers["X-Tenant-Id"], "enterprise-1")

    async def test_config_request_url_and_headers_follow_international_endpoint(self):
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://www.codebuddy.ai"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://www.codebuddy.ai"
        client = FakeConfigClient(response=FakeConfigResponse({
            "code": 0,
            "data": {"models": [{"id": "international-model"}]},
        }))
        manager = self._make_manager(client)

        models = await manager.get_actual_models_for_credential(
            self._user(),
            "international-credential",
            {"bearer_token": "international-token", "user_id": "user-id"},
        )

        self.assertEqual(models, ["international-model"])
        self.assertEqual(client.requests[0]["url"], "https://www.codebuddy.ai/v3/config")
        self.assertEqual(client.requests[0]["headers"]["Host"], "www.codebuddy.ai")
        self.assertEqual(client.requests[0]["headers"]["X-Domain"], "www.codebuddy.ai")

    async def test_configured_models_are_user_scoped(self):
        config.update_settings({"CODEBUDDY_MODELS": "admin-model"}, username="admin")
        config.update_settings({"CODEBUDDY_MODELS": "alice-model"}, username="alice")
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value", "user_id": "user-id"}),
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
                return_value=FakeTokenManager({"bearer_token": "token-value", "user_id": "user-id"}),
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

        expired_credential = {"bearer_token": "expired-token", "user_id": "expired-user"}
        valid_credential = {"bearer_token": "valid-token", "user_id": "valid-user"}
        token_manager = mock.Mock()
        token_manager.get_current_credential_info.return_value = {
            "credential_id": "expired-credential"
        }
        token_manager.get_credential_by_id.return_value = expired_credential
        token_manager.is_token_expired.return_value = True
        token_manager.preview_next_credential.return_value = (
            "valid-credential",
            valid_credential,
        )

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=token_manager,
        ):
            models = await manager.get_actual_models(self._user())

        self.assertEqual(models, ["valid-model"])
        token_manager.is_token_expired.assert_called_once_with(expired_credential)
        token_manager.preview_next_credential.assert_called_once_with()
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
                return_value=FakeTokenManager({"bearer_token": "token-value", "user_id": "user-id"}),
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
                return_value=FakeTokenManager({"bearer_token": "token-value", "user_id": "user-id"}),
        ):
            model = await manager.get_first_actual_model(self._user())

        self.assertEqual(model, "real-first")

    async def test_does_not_return_stale_cache_when_upstream_fails(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a, shared"}, username="admin")
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))
        manager._models_cache["admin:credential-id"] = ["shared", "cached-b"]
        manager._models_cache_expires_at["admin:credential-id"] = 0

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value", "user_id": "user-id"}),
        ):
            models = await manager.get_available_models(self._user())

        self.assertEqual(models, ["manual-a", "shared"])
        self.assertNotIn("admin:credential-id", manager._models_cache)
        self.assertNotIn("admin:credential-id", manager._models_cache_expires_at)

    async def test_concurrent_cache_misses_share_one_upstream_request(self):
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        class BlockingClient(FakeConfigClient):
            async def get(self, url, headers=None, **_kwargs):
                self.requests.append({"url": url, "headers": headers})
                request_started.set()
                await release_request.wait()
                return FakeConfigResponse({
                    "code": 0,
                    "data": {"models": [{"id": "shared-model"}]},
                })

        client = BlockingClient()
        manager = self._make_manager(client)
        credential = {"bearer_token": "token", "user_id": "user"}
        first = asyncio.create_task(
            manager.get_actual_models_for_credential(
                self._user(), "credential-id", credential,
            )
        )
        await request_started.wait()
        second = asyncio.create_task(
            manager.get_actual_models_for_credential(
                self._user(), "credential-id", credential,
            )
        )
        await asyncio.sleep(0)
        self.assertEqual(len(client.requests), 1)

        release_request.set()
        self.assertEqual(await first, ["shared-model"])
        self.assertEqual(await second, ["shared-model"])
        self.assertEqual(len(client.requests), 1)

    async def test_cache_can_be_evicted_when_credential_is_deleted(self):
        manager = self._make_manager(FakeConfigClient())
        manager._models_cache["admin:deleted-id"] = ["old-model"]
        manager._models_cache_expires_at["admin:deleted-id"] = 9999

        manager.invalidate_credential(self._user(), "deleted-id")

        self.assertNotIn("admin:deleted-id", manager._models_cache)
        self.assertNotIn("admin:deleted-id", manager._models_cache_expires_at)

    async def test_cancelled_only_waiter_does_not_leave_completed_inflight_task(self):
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        class DelayedClient:
            async def get(self, _url, headers=None, **_kwargs):
                del headers
                request_started.set()
                await release_request.wait()
                return FakeConfigResponse({
                    "code": 0,
                    "data": {"models": [{"id": "completed-model"}]},
                })

        manager = self._make_manager(DelayedClient())
        waiter = asyncio.create_task(manager.get_actual_models_for_credential(
            self._user(),
            "cancelled-waiter",
            {"bearer_token": "token", "user_id": "user"},
        ))
        await request_started.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter

        self.assertIn("admin:cancelled-waiter", manager._inflight)
        release_request.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertNotIn("admin:cancelled-waiter", manager._inflight)
        self.assertEqual(
            manager._models_cache["admin:cancelled-waiter"],
            ["completed-model"],
        )

    async def test_cancelled_only_waiter_consumes_background_failure(self):
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        class FailingClient:
            async def get(self, _url, headers=None, **_kwargs):
                del headers
                request_started.set()
                await release_request.wait()
                raise RuntimeError("background request failed")

        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        exception_contexts = []
        loop.set_exception_handler(
            lambda _loop, context: exception_contexts.append(context)
        )
        try:
            manager = self._make_manager(FailingClient())
            waiter = asyncio.create_task(manager.get_actual_models_for_credential(
                self._user(),
                "failed-cancelled-waiter",
                {"bearer_token": "token", "user_id": "user"},
            ))
            await request_started.wait()
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter

            release_request.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            self.assertNotIn("admin:failed-cancelled-waiter", manager._inflight)
            self.assertEqual(exception_contexts, [])
        finally:
            release_request.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            loop.set_exception_handler(previous_exception_handler)

    async def test_cancelled_inflight_task_is_cleaned_without_callback_error(self):
        request_started = asyncio.Event()

        class BlockingClient:
            async def get(self, _url, headers=None, **_kwargs):
                del headers
                request_started.set()
                await asyncio.Event().wait()

        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        exception_contexts = []
        loop.set_exception_handler(
            lambda _loop, context: exception_contexts.append(context)
        )
        try:
            manager = self._make_manager(BlockingClient())
            waiter = asyncio.create_task(manager.get_actual_models_for_credential(
                self._user(),
                "cancelled-inflight",
                {"bearer_token": "token", "user_id": "user"},
            ))
            await request_started.wait()
            manager._inflight["admin:cancelled-inflight"].cancel()

            with self.assertRaises(asyncio.CancelledError):
                await waiter
            await asyncio.sleep(0)

            self.assertNotIn("admin:cancelled-inflight", manager._inflight)
            self.assertEqual(exception_contexts, [])
        finally:
            loop.set_exception_handler(previous_exception_handler)

    async def test_inflight_query_cannot_repopulate_reused_credential_cache(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        release_second = asyncio.Event()

        class ReusedCredentialClient:
            def __init__(self):
                self.requests = []

            async def get(self, url, headers=None, **_kwargs):
                request_index = len(self.requests)
                self.requests.append({"url": url, "headers": headers})
                if request_index == 0:
                    first_started.set()
                    await release_first.wait()
                    model = "old-model"
                else:
                    await release_second.wait()
                    model = "new-model"
                return FakeConfigResponse({
                    "code": 0,
                    "data": {"models": [{"id": model}]},
                })

        client = ReusedCredentialClient()
        manager = self._make_manager(client)
        first = asyncio.create_task(manager.get_actual_models_for_credential(
            self._user(),
            "reused-id",
            {"bearer_token": "old-token", "user_id": "old-user"},
        ))
        await first_started.wait()
        manager.invalidate_credential(self._user(), "reused-id")
        second = asyncio.create_task(manager.get_actual_models_for_credential(
            self._user(),
            "reused-id",
            {"bearer_token": "new-token", "user_id": "new-user"},
        ))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        try:
            self.assertEqual(len(client.requests), 2)
            release_second.set()
            self.assertEqual(await second, ["new-model"])
            release_first.set()
            self.assertEqual(await first, ["old-model"])
            self.assertEqual(manager._models_cache["admin:reused-id"], ["new-model"])
        finally:
            release_first.set()
            release_second.set()
            await asyncio.gather(first, second, return_exceptions=True)

    async def test_expired_credential_never_reuses_fresh_model_cache(self):
        client = FakeConfigClient(response=FakeConfigResponse({
            "code": 0,
            "data": {"models": [{"id": "refreshed-model"}]},
        }))
        manager = self._make_manager(client)
        manager._models_cache["admin:expired-id"] = ["cached-model"]
        manager._models_cache_expires_at["admin:expired-id"] = float("inf")

        models = await manager.get_actual_models_for_credential(
            self._user(),
            "expired-id",
            {
                "bearer_token": "expired-token",
                "user_id": "expired-user",
                "created_at": 1,
                "expires_in": 1,
            },
        )

        self.assertEqual(models, ["refreshed-model"])
        self.assertEqual(len(client.requests), 1)

    async def test_falls_back_to_configured_models_when_no_cached_models_exist(self):
        config.update_settings({"CODEBUDDY_MODELS": "manual-a,"}, username="admin")
        manager = self._make_manager(FakeConfigClient(error=RuntimeError("upstream down")))

        with mock.patch(
                "src.models_manager.get_token_manager_for_user",
                return_value=FakeTokenManager({"bearer_token": "token-value", "user_id": "user-id"}),
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


    async def test_get_actual_models_rejects_selected_credential_without_id(self):
        manager = self._make_manager(FakeConfigClient())
        token_manager = FakeTokenManager({"bearer_token": "token"}, credential_id="")

        with mock.patch("src.models_manager.get_token_manager_for_user", return_value=token_manager):
            with self.assertRaisesRegex(RuntimeError, "credential_id"):
                await manager.get_actual_models(self._user())

    async def test_first_model_helpers_reject_empty_results(self):
        manager = self._make_manager(FakeConfigClient())
        with mock.patch.object(manager, "get_actual_models", new=mock.AsyncMock(return_value=[])):
            with self.assertRaisesRegex(RuntimeError, "没有可用模型"):
                await manager.get_first_actual_model(self._user())

        with mock.patch.object(
            manager,
            "get_actual_models_for_credential",
            new=mock.AsyncMock(return_value=[]),
        ):
            with self.assertRaisesRegex(RuntimeError, "没有可用模型"):
                await manager.get_first_actual_model_for_credential(self._user(), "id", {})

    async def test_first_model_for_credential_returns_first_result(self):
        manager = self._make_manager(FakeConfigClient())
        with mock.patch.object(
            manager,
            "get_actual_models_for_credential",
            new=mock.AsyncMock(return_value=["first", "second"]),
        ):
            result = await manager.get_first_actual_model_for_credential(self._user(), "id", {})

        self.assertEqual(result, "first")

    def test_credential_cache_key_rejects_blank_id(self):
        with self.assertRaisesRegex(RuntimeError, "credential_id"):
            ModelsManager._credential_cache_key(self._user(), " ")

    async def test_fetch_models_rejects_missing_token_http_and_api_errors(self):
        with self.assertRaisesRegex(RuntimeError, "bearer_token"):
            await self._make_manager(FakeConfigClient())._fetch_models_from_codebuddy_credential({})

        error_responses = [
            (FakeConfigResponse({}, status_code=503, text="unavailable"), "HTTP 503"),
            (FakeConfigResponse({"code": 42, "msg": "denied"}), "错误代码 42"),
        ]
        for response, expected in error_responses:
            with self.subTest(expected=expected):
                manager = self._make_manager(FakeConfigClient(response=response))
                with self.assertRaisesRegex(RuntimeError, expected):
                    await manager._fetch_models_from_codebuddy_credential({
                        "bearer_token": "token",
                        "user_id": "user-id",
                    })

    async def test_model_lookup_errors_never_include_upstream_response_text_or_message(self):
        sensitive = "sensitive-upstream-body"
        responses = [
            FakeConfigResponse({}, status_code=503, text=sensitive),
            FakeConfigResponse({"code": 42, "msg": sensitive}),
        ]

        for response in responses:
            with self.subTest(status=response.status_code):
                manager = self._make_manager(FakeConfigClient(response=response))
                with self.assertRaises(RuntimeError) as raised:
                    await manager._fetch_models_from_codebuddy_credential({
                        "bearer_token": "token",
                        "user_id": "user-id",
                    })
                self.assertNotIn(sensitive, str(raised.exception))

    async def test_model_lookup_rejects_invalid_json_and_non_object_body(self):
        invalid_json = FakeConfigResponse({})
        invalid_json.json = mock.Mock(side_effect=ValueError("sensitive raw JSON"))
        non_object = FakeConfigResponse([])

        for response, expected in (
            (invalid_json, "无效 JSON"),
            (non_object, "不是对象"),
        ):
            with self.subTest(expected=expected):
                manager = self._make_manager(FakeConfigClient(response=response))
                with self.assertRaisesRegex(RuntimeError, expected):
                    await manager._fetch_models_from_codebuddy_credential({
                        "bearer_token": "token",
                        "user_id": "user-id",
                    })

    async def test_models_manager_accepts_async_shared_client_factory(self):
        client = FakeConfigClient(response=FakeConfigResponse({
            "code": 0,
            "data": {"models": [{"id": "model"}]},
        }))
        manager = ModelsManager(http_client_factory=mock.AsyncMock(return_value=client))

        models = await manager._fetch_models_from_codebuddy_credential({
            "bearer_token": "token",
            "user_id": "user-id",
        })

        self.assertEqual(models, ["model"])

    def test_extract_model_ids_rejects_invalid_response_shapes(self):
        invalid_bodies = [
            ({"data": None}, "data 不是对象"),
            ({"data": {"models": None}}, "有效 models 列表"),
            ({"data": {"models": ["invalid"]}}, "非对象项"),
            ({"data": {"models": [{"id": " "}]}}, "没有有效 id"),
        ]

        for body, expected in invalid_bodies:
            with self.subTest(body=body):
                with self.assertRaisesRegex(RuntimeError, expected):
                    ModelsManager._extract_model_ids(body)


if __name__ == "__main__":
    unittest.main()
