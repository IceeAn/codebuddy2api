import time
import unittest
from unittest import mock

import httpx

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.session_store import session_store
from src.stream_service import StreamObservation
from src.usage_stats_middleware import dropped_completion_events
from src.usage_stats_store import usage_stats_store
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class FakeTokenManager:
    def get_next_credential(self):
        return {
            "bearer_token": "upstream-secret",
            "user_id": "upstream-user",
            "domain": "codebuddy.example",
        }

    @staticmethod
    def get_current_credential_info():
        return {
            "credential_id": "credential-1",
            "filename": "credential.json",
        }


class ObservedCompletionService:
    def __init__(self, observer=None):
        self.observer = observer

    async def handle_non_stream_response(self, _payload, _headers, *, response_model):
        self.observer(StreamObservation(
            kind="upstream_event",
            has_reasoning_content=True,
        ))
        self.observer(StreamObservation(
            kind="upstream_event",
            has_content=True,
            finish_reason="stop",
            usage={
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
                "completion_tokens_details": {"reasoning_tokens": 5},
                "prompt_cache_hit_tokens": 4,
                "prompt_cache_miss_tokens": 8,
                "credit": 0.3,
            },
        ))
        self.observer(StreamObservation(kind="upstream_event", upstream_done=True))
        return {
            "id": "chatcmpl-test",
            "model": response_model,
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }


class UsageStatsIntegrationTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.api_key_record = api_key_store.create_key("admin", "production")
        self.session_id = session_store.create("admin")
        usage_stats_store.reset_dropped_events_for_tests()
        dropped_completion_events.reset_for_tests()

    def tearDown(self):
        usage_stats_store.reset_dropped_events_for_tests()
        dropped_completion_events.reset_for_tests()
        super().tearDown()

    async def _request(
            self,
            method,
            path,
            *,
            api_key=False,
            session=False,
            json=None,
            content=None,
    ):
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {self.api_key_record['api_key']}"
        if session:
            headers["Cookie"] = f"{SESSION_COOKIE_NAME}={self.session_id}"
        if content is not None:
            headers["Content-Type"] = "application/json"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.request(
                method,
                path,
                headers=headers,
                json=json,
                content=content,
            )

    async def _send_chat(self):
        with (
            mock.patch(
                "src.openai_router.get_token_manager_for_user",
                return_value=FakeTokenManager(),
            ),
            mock.patch(
                "src.openai_router.CodeBuddyStreamService",
                ObservedCompletionService,
            ),
        ):
            return await self._request(
                "POST",
                "/openai/v1/chat/completions",
                api_key=True,
                json={
                    "model": "provider/glm-5.2",
                    "messages": [{"role": "user", "content": "never persist this prompt"}],
                    "tools": [{
                        "type": "function",
                        "function": {
                            "name": "private-tool",
                            "parameters": {"type": "object"},
                        },
                    }],
                },
            )

    async def test_external_chat_is_persisted_and_read_through_session_stats_api(self):
        response = await self._send_chat()
        self.assertEqual(response.status_code, 200)

        now = int(time.time())
        overview = await self._request(
            "GET",
            f"/api/admin/stats/overview?start_at={now - 30}&end_at={now + 30}"
            "&timezone=Asia%2FTaipei&traffic=external",
            session=True,
        )
        details = await self._request(
            "GET",
            f"/api/admin/stats/requests?start_at={now - 30}&end_at={now + 30}",
            session=True,
        )

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["totals"]["request_count"], 1)
        self.assertEqual(overview.json()["totals"]["total_tokens"], 20)
        self.assertEqual(overview.json()["totals"]["total_credit"], 0.3)
        items = details.json()["items"]
        self.assertEqual(len(items), 1)
        event = items[0]
        self.assertEqual(event["source"], "external_api")
        self.assertEqual(event["requested_model"], "glm-5.2")
        self.assertEqual(event["upstream_model"], "glm-5.2")
        self.assertEqual(event["api_key_id"], self.api_key_record["id"])
        self.assertEqual(event["api_key_name"], "production")
        self.assertEqual(event["credential_id"], "credential-1")
        self.assertEqual(event["credential_label"], "credential.json")
        self.assertEqual(event["outcome"], "success")
        self.assertEqual(event["message_count"], 1)
        self.assertEqual(event["tool_count"], 1)
        self.assertEqual(event["reasoning_tokens"], 5)
        serialized = str(event)
        self.assertNotIn("never persist this prompt", serialized)
        self.assertNotIn("private-tool", serialized)
        self.assertNotIn("upstream-secret", serialized)

    async def test_stats_write_failure_keeps_chat_success_and_reports_data_loss(self):
        with mock.patch.object(
            usage_stats_store,
            "_record_event",
            side_effect=OSError("disk unavailable"),
        ), self.assertLogs("src.usage_stats_store", level="ERROR"):
            response = await self._send_chat()

        overview = await self._request(
            "GET",
            "/api/admin/stats/overview?timezone=UTC",
            session=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(overview.json()["totals"]["request_count"], 0)
        self.assertEqual(overview.json()["data_quality"]["dropped_events"], 1)

    async def test_authenticated_credential_test_validation_failure_is_counted(self):
        response = await self._request(
            "POST",
            "/api/admin/credentials/credential-1/test",
            session=True,
            json=[],
        )
        details = await self._request(
            "GET",
            "/api/admin/stats/requests?traffic=admin",
            session=True,
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(details.json()["items"]), 1)
        event = details.json()["items"][0]
        self.assertEqual(event["source"], "credential_test")
        self.assertEqual(event["outcome"], "failure")
        self.assertEqual(event["http_status"], 422)
        self.assertEqual(event["error_type"], "validation_error")
        self.assertGreater(event["request_bytes"], 0)
        self.assertEqual(event["credential_id"], "credential-1")
        self.assertEqual(event["credential_label"], "credential-1")

    async def test_invalid_json_still_records_authenticated_request_bytes(self):
        body = b'{"messages": invalid}'
        response = await self._request(
            "POST",
            "/openai/v1/chat/completions",
            api_key=True,
            content=body,
        )
        details = await self._request(
            "GET",
            "/api/admin/stats/requests",
            session=True,
        )

        self.assertEqual(response.status_code, 400)
        event = details.json()["items"][0]
        self.assertEqual(event["request_bytes"], len(body))
        self.assertEqual(event["requested_model"], "unknown")
        self.assertIsNone(event["message_count"])

    async def test_missing_credential_preserves_safe_client_request_shape(self):
        class EmptyTokenManager:
            @staticmethod
            def get_next_credential():
                return None

        body = {
            "model": "provider/model-a",
            "stream": True,
            "messages": [{"role": "user", "content": "private prompt"}],
            "tools": [{"private": "tool definition"}],
        }
        with mock.patch(
            "src.openai_router.get_token_manager_for_user",
            return_value=EmptyTokenManager(),
        ):
            response = await self._request(
                "POST",
                "/openai/v1/chat/completions",
                api_key=True,
                json=body,
            )
        details = await self._request(
            "GET",
            "/api/admin/stats/requests",
            session=True,
        )

        self.assertEqual(response.status_code, 401)
        event = details.json()["items"][0]
        self.assertEqual(event["requested_model"], "unknown")
        self.assertIs(event["client_stream"], True)
        self.assertEqual(event["message_count"], 1)
        self.assertEqual(event["tool_count"], 1)
        self.assertIsNone(event["upstream_model"])
        self.assertNotIn("private prompt", str(event))
        self.assertNotIn("tool definition", str(event))

    async def test_object_model_cannot_reenter_stats_after_request_preparation(self):
        with (
            mock.patch(
                "src.openai_router.get_token_manager_for_user",
                return_value=FakeTokenManager(),
            ),
            mock.patch(
                "src.openai_router.CodeBuddyStreamService",
                ObservedCompletionService,
            ),
        ):
            response = await self._request(
                "POST",
                "/openai/v1/chat/completions",
                api_key=True,
                json={
                    "model": {"private-model-field": "must-not-persist"},
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        details = await self._request(
            "GET",
            "/api/admin/stats/requests",
            session=True,
        )

        self.assertEqual(response.status_code, 200)
        event = details.json()["items"][0]
        self.assertEqual(event["requested_model"], "unknown")
        self.assertIsNone(event["upstream_model"])
        self.assertNotIn("private-model-field", str(event))
        self.assertNotIn("must-not-persist", str(event))


if __name__ == "__main__":
    unittest.main()
