import unittest
from unittest import mock

import config
import httpx

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.session_store import session_store
from src.stream_service import UpstreamAPIError
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class ProtocolRouteAuthenticationTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.api_key = api_key_store.create_key("admin", "external")["api_key"]
        self.session_id = session_store.create("admin")

    async def _request(self, method, path, *, api_key=False, session=False, json=None):
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if session:
            headers["Cookie"] = f"{SESSION_COOKIE_NAME}={self.session_id}"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.request(method, path, headers=headers, json=json)

    @mock.patch(
        "src.openai_router.get_available_models_list",
        new_callable=mock.AsyncMock,
        return_value=["model-a"],
    )
    async def test_openai_routes_only_accept_api_keys(self, _get_models):
        accepted = await self._request("GET", "/openai/v1/models", api_key=True)
        rejected = await self._request("GET", "/openai/v1/models", session=True)

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["data"][0]["id"], "model-a")
        self.assertEqual(accepted.headers["Cache-Control"], "private, no-store")
        self.assertEqual(rejected.status_code, 401)

    @mock.patch(
        "src.openai_router.get_available_models_list",
        new_callable=mock.AsyncMock,
        return_value=["model-a"],
    )
    async def test_playground_openai_routes_only_accept_sessions(self, _get_models):
        path = "/api/admin/playground/openai/v1/models"
        accepted = await self._request("GET", path, session=True)
        rejected = await self._request("GET", path, api_key=True)
        obsolete = await self._request("GET", "/codebuddy/v1/models", session=True)

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["data"][0]["id"], "model-a")
        self.assertEqual(accepted.headers["Cache-Control"], "private, no-store")
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(obsolete.status_code, 404)

    @mock.patch(
        "src.openai_router.chat_completions",
        new_callable=mock.AsyncMock,
        return_value={"shared": True},
    )
    async def test_both_chat_routes_use_the_same_protocol_handler(self, chat_completions):
        external_context = mock.Mock()
        admin_context = mock.Mock()
        with mock.patch(
            "src.openai_router.create_usage_stats_context",
            side_effect=[external_context, admin_context],
        ) as create_context:
            external = await self._request(
                "POST",
                "/openai/v1/chat/completions",
                api_key=True,
                json={"model": "model-a", "messages": []},
            )
            playground = await self._request(
                "POST",
                "/api/admin/playground/openai/v1/chat/completions",
                session=True,
                json={"model": "model-a", "messages": []},
            )
            obsolete = await self._request(
                "POST",
                "/codebuddy/v1/chat/completions",
                session=True,
                json={"model": "model-a", "messages": []},
            )

        self.assertEqual(external.json(), {"shared": True})
        self.assertEqual(playground.json(), {"shared": True})
        self.assertEqual(obsolete.status_code, 404)
        self.assertEqual(chat_completions.await_count, 2)
        self.assertEqual(create_context.call_args_list[0].args[2], "external_api")
        self.assertEqual(create_context.call_args_list[1].args[2], "admin_playground")
        self.assertIsNotNone(create_context.call_args_list[0].args[1].api_key_id)
        self.assertIsNone(create_context.call_args_list[1].args[1].api_key_id)
        self.assertIs(
            chat_completions.await_args_list[0].kwargs["stats_context"],
            external_context,
        )
        self.assertIs(
            chat_completions.await_args_list[1].kwargs["stats_context"],
            admin_context,
        )
        self.assertGreater(chat_completions.await_args_list[0].kwargs["request_bytes"], 0)

    @mock.patch(
        "src.openai_router.chat_completions",
        new_callable=mock.AsyncMock,
        side_effect=UpstreamAPIError(
            status_code=429,
            message="quota exhausted",
            error_type="quota_error",
            code="quota",
            headers={"Retry-After": "7"},
        ),
    )
    async def test_upstream_error_uses_openai_error_envelope(self, _chat_completions):
        response = await self._request(
            "POST",
            "/openai/v1/chat/completions",
            api_key=True,
            json={"model": "model-a", "messages": []},
        )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {
            "error": {
                "message": "quota exhausted",
                "type": "quota_error",
                "code": "quota",
            },
        })
        self.assertEqual(response.headers["Retry-After"], "7")
        self.assertNotIn("WWW-Authenticate", response.headers)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")


if __name__ == "__main__":
    unittest.main()
