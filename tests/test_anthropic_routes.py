import asyncio
import unittest
from unittest import mock

import httpx
from fastapi.responses import JSONResponse

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.chat_execution import CodeBuddyCredentialError
from src.session_store import session_store
from src.stream_service import UpstreamAPIError
from src.auth_types import AuthenticatedUser
from src.anthropic_router import require_anthropic_session_user
from tests.helpers import TempConfigMixin, configure_users_file, make_request
from web import app, upstream_api_error_handler


class AnthropicRouteTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.api_key = api_key_store.create_key("admin", "anthropic")['api_key']
        self.session_id = session_store.create("admin")
        self.version = {"anthropic-version": "2023-06-01"}

    async def _request(
            self,
            method,
            path,
            *,
            headers=None,
            json=None,
            content=None,
            raise_app_exceptions=True,
    ):
        transport = httpx.ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.request(method, path, headers=headers, json=json, content=content)

    def _bearer(self):
        return {**self.version, "Authorization": f"Bearer {self.api_key}"}

    def _x_api_key(self):
        return {**self.version, "x-api-key": self.api_key}

    def _session(self):
        return {**self.version, "Cookie": f"{SESSION_COOKIE_NAME}={self.session_id}"}

    @mock.patch(
        "src.anthropic_router._available_models",
        new_callable=mock.AsyncMock,
        return_value=["glm-5.2", "vendor/model"],
    )
    async def test_models_support_both_api_key_headers_and_exact_synthetic_ids(self, _models):
        for headers in (self._x_api_key(), self._bearer(), {
            **self._x_api_key(),
            "Authorization": f"Bearer {self.api_key}",
        }):
            with self.subTest(headers=list(headers)):
                response = await self._request(
                    "GET",
                    "/anthropic/v1/models?limit=1000",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["data"], [
                    {"id": "anthropic/codebuddy/glm-5.2", "display_name": "glm-5.2"},
                    {"id": "anthropic/codebuddy/vendor/model", "display_name": "vendor/model"},
                ])
                self.assertEqual(response.headers["request-id"], response.headers["request-id"])
                self.assertTrue(response.headers["request-id"].startswith("req_"))
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_external_authentication_failures_use_anthropic_envelope_once(self):
        cases = [
            self.version,
            {**self.version, "x-api-key": "sk-invalid"},
            {**self.version, "Authorization": self.api_key},
            {**self.version, "x-api-key": "   "},
            {
                **self.version,
                "x-api-key": self.api_key,
                "Authorization": "Bearer sk-conflict",
            },
            {**self.version, "Cookie": f"{SESSION_COOKIE_NAME}={self.session_id}"},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                with mock.patch.object(api_key_store, "verify", wraps=api_key_store.verify) as verify:
                    response = await self._request("GET", "/anthropic/v1/models", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["type"], "error")
                self.assertEqual(response.json()["error"]["type"], "authentication_error")
                self.assertEqual(response.json()["request_id"], response.headers["request-id"])
                self.assertLessEqual(verify.call_count, 1)
                self.assertNotIn("WWW-Authenticate", response.headers)

        with mock.patch("src.anthropic_router.users_store.has_users_file", return_value=False):
            missing_users = await self._request(
                "GET",
                "/anthropic/v1/models",
                headers=self._x_api_key(),
            )
        self.assertEqual(missing_users.status_code, 500)
        self.assertEqual(missing_users.json()["error"]["type"], "api_error")

    async def test_api_key_store_failure_uses_anthropic_error_envelope(self):
        with mock.patch.object(
            api_key_store,
            "verify",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = await self._request(
                "GET",
                "/anthropic/v1/models",
                headers=self._x_api_key(),
                raise_app_exceptions=False,
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["type"], "error")
        self.assertEqual(response.json()["error"]["type"], "api_error")
        self.assertEqual(response.json()["error"]["message"], "API key verification failed")
        self.assertEqual(response.json()["request_id"], response.headers["request-id"])

    @mock.patch(
        "src.anthropic_router._available_models",
        new_callable=mock.AsyncMock,
        return_value=["glm"],
    )
    async def test_playground_only_accepts_session_and_is_protocol_isolated(self, _models):
        path = "/api/admin/playground/anthropic/v1/models"
        accepted = await self._request("GET", path, headers=self._session())
        api_key_rejected = await self._request("GET", path, headers=self._bearer())
        external_cookie_rejected = await self._request(
            "GET",
            "/anthropic/v1/models",
            headers=self._session(),
        )
        root_missing = await self._request("GET", "/v1/models", headers=self._bearer())

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(api_key_rejected.status_code, 401)
        self.assertEqual(api_key_rejected.json()["error"]["type"], "authentication_error")
        self.assertEqual(api_key_rejected.headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(external_cookie_rejected.status_code, 401)
        self.assertEqual(root_missing.status_code, 404)

    async def test_version_beta_and_query_beta_contract(self):
        missing = await self._request(
            "GET",
            "/anthropic/v1/models",
            headers={"x-api-key": self.api_key},
        )
        wrong = await self._request(
            "GET",
            "/anthropic/v1/models",
            headers={**self._x_api_key(), "anthropic-version": "2024-01-01"},
        )
        with mock.patch(
            "src.anthropic_router._available_models",
            new=mock.AsyncMock(return_value=[]),
        ):
            beta = await self._request(
                "GET",
                "/anthropic/v1/models",
                headers={**self._x_api_key(), "anthropic-beta": "tools-2025"},
            )
            query_beta = await self._request(
                "GET",
                "/anthropic/v1/models?beta=true",
                headers=self._x_api_key(),
            )

        for response in (missing, wrong):
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["type"], "invalid_request_error")
            self.assertEqual(response.json()["request_id"], response.headers["request-id"])
        self.assertEqual(beta.status_code, 200)
        self.assertTrue(beta.headers["request-id"].startswith("req_"))
        self.assertEqual(query_beta.status_code, 200)

    async def test_unknown_anthropic_routes_and_methods_use_anthropic_errors(self):
        not_found = await self._request("GET", "/anthropic/v1/not-found")
        method_not_allowed = await self._request("GET", "/anthropic/v1/messages")

        for response, status, error_type in (
            (not_found, 404, "not_found_error"),
            (method_not_allowed, 405, "invalid_request_error"),
        ):
            with self.subTest(status=status):
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()["type"], "error")
                self.assertEqual(response.json()["error"]["type"], error_type)
                self.assertEqual(response.json()["request_id"], response.headers["request-id"])

    async def test_count_tokens_returns_documented_not_found(self):
        response = await self._request(
            "POST",
            "/anthropic/v1/messages/count_tokens",
            headers=self._x_api_key(),
            json={"model": "glm", "messages": []},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["type"], "not_found_error")
        self.assertEqual(response.json()["request_id"], response.headers["request-id"])

    @mock.patch("src.anthropic_router.create_usage_stats_context")
    @mock.patch("src.anthropic_router.execute_codebuddy_chat", new_callable=mock.AsyncMock)
    async def test_message_request_is_translated_prepared_and_returns_request_id(
            self,
            execute,
            create_stats,
    ):
        stats = create_stats.return_value
        execute.return_value = {
            "id": "msg_fixed",
            "type": "message",
            "role": "assistant",
            "model": "anthropic/codebuddy/glm-5.2",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        body = {
            "model": "anthropic/codebuddy/glm-5.2",
            "max_tokens": 64,
            "system": "You are Claude.",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"user_id": "must-not-forward"},
        }
        response = await self._request(
            "POST",
            "/anthropic/v1/messages?beta=true",
            headers={
                **self._x_api_key(),
                "x-claude-code-session-id": "must-not-persist",
            },
            json=body,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "message")
        self.assertTrue(response.headers["request-id"].startswith("req_"))
        prepared = execute.await_args.args[0]
        self.assertEqual(prepared.response_model, "anthropic/codebuddy/glm-5.2")
        self.assertEqual(prepared.payload["model"], "glm-5.2")
        self.assertTrue(prepared.payload["stream"])
        self.assertTrue(prepared.payload["stream_options"]["include_usage"])
        self.assertNotIn("metadata", prepared.payload)
        self.assertNotIn("must-not-persist", str(prepared.payload))
        stats.capture_request_shape.assert_called_once_with(body)
        self.assertGreater(stats.capture_request_bytes.call_args.args[0], 0)

    @mock.patch("src.anthropic_router.execute_codebuddy_chat", new_callable=mock.AsyncMock)
    @mock.patch("src.anthropic_router._available_models", new_callable=mock.AsyncMock)
    async def test_message_model_skips_discovery_and_follows_namespace_setting(
            self,
            available_models,
            execute,
    ):
        execute.return_value = {"type": "message"}
        body = {
            "model": "anthropic/codebuddy/not-discovered",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "hello"}],
        }
        with mock.patch("config.get_strip_model_namespace", return_value=True):
            stripped = await self._request(
                "POST",
                "/anthropic/v1/messages",
                headers=self._x_api_key(),
                json=body,
            )

        self.assertEqual(stripped.status_code, 200)
        available_models.assert_not_awaited()
        self.assertEqual(execute.await_args.args[0].payload["model"], "not-discovered")

        with mock.patch("config.get_strip_model_namespace", return_value=False):
            preserved = await self._request(
                "POST",
                "/anthropic/v1/messages",
                headers=self._x_api_key(),
                json=body,
            )

        self.assertEqual(preserved.status_code, 200)
        available_models.assert_not_awaited()
        self.assertEqual(
            execute.await_args.args[0].payload["model"],
            "anthropic/codebuddy/not-discovered",
        )

        invalid = await self._request(
            "POST",
            "/anthropic/v1/messages",
            headers=self._x_api_key(),
            json=[],
        )
        self.assertEqual(invalid.status_code, 400)
        available_models.assert_not_awaited()

    @mock.patch("src.anthropic_router.create_usage_stats_context")
    @mock.patch("src.anthropic_router.execute_codebuddy_chat", new_callable=mock.AsyncMock)
    async def test_validation_credential_and_upstream_errors_are_mapped(
            self,
            execute,
            create_stats,
    ):
        invalid = await self._request(
            "POST",
            "/anthropic/v1/messages",
            headers=self._x_api_key(),
            json={"model": "glm", "max_tokens": True, "messages": []},
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["type"], "invalid_request_error")

        valid = {"model": "glm", "max_tokens": 10, "messages": [{"role": "user", "content": "x"}]}
        execute.side_effect = CodeBuddyCredentialError("No credential")
        no_credential = await self._request(
            "POST", "/anthropic/v1/messages", headers=self._x_api_key(), json=valid
        )
        self.assertEqual(no_credential.status_code, 500)
        self.assertEqual(no_credential.json()["error"]["type"], "api_error")

        mappings = [
            (429, 429, 429, "rate_limit_error"),
            (502, 529, 529, "overloaded_error"),
            (504, None, 504, "timeout_error"),
            (502, 504, 504, "timeout_error"),
            (502, 500, 502, "api_error"),
        ]
        for downstream, upstream, expected_status, expected_type in mappings:
            with self.subTest(upstream=upstream):
                execute.side_effect = UpstreamAPIError(
                    downstream,
                    "safe failure",
                    "upstream_timeout" if downstream == 504 else "upstream_error",
                    headers={"Retry-After": "7", "WWW-Authenticate": "secret"},
                    upstream_status_code=upstream,
                )
                response = await self._request(
                    "POST", "/anthropic/v1/messages", headers=self._x_api_key(), json=valid
                )
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["error"]["type"], expected_type)
                self.assertEqual(response.headers["Retry-After"], "7")
                self.assertNotIn("WWW-Authenticate", response.headers)

    @mock.patch("src.anthropic_router.create_usage_stats_context")
    async def test_invalid_json_stream_return_internal_error_and_model_failure(self, create_stats):
        invalid_json = await self._request(
            "POST",
            "/anthropic/v1/messages",
            headers={**self._x_api_key(), "Content-Type": "application/json"},
            content=b'{"model":',
        )
        self.assertEqual(invalid_json.status_code, 400)
        create_stats.return_value.mark_failure.assert_called_with("validation_error", 400)

        valid = {
            "model": "glm",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        }
        with mock.patch(
            "src.anthropic_router.execute_codebuddy_chat",
            new=mock.AsyncMock(return_value=JSONResponse({"stream": True})),
        ):
            streamed = await self._request(
                "POST", "/anthropic/v1/messages", headers=self._x_api_key(), json=valid
            )
        self.assertEqual(streamed.json(), {"stream": True})

        with mock.patch(
            "src.anthropic_router.execute_codebuddy_chat",
            new=mock.AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            internal = await self._request(
                "POST", "/anthropic/v1/messages", headers=self._x_api_key(), json={**valid, "stream": False}
            )
        self.assertEqual(internal.status_code, 500)
        self.assertEqual(internal.json()["error"]["message"], "Internal server error")
        create_stats.return_value.mark_failure.assert_called_with("internal_error", 500)

        with mock.patch(
            "src.anthropic_router._available_models",
            new=mock.AsyncMock(side_effect=RuntimeError("models")),
        ):
            models_error = await self._request(
                "GET", "/anthropic/v1/models", headers=self._x_api_key()
            )
        self.assertEqual(models_error.status_code, 500)

    async def test_session_internal_error_available_models_and_web_upstream_handler(self):
        request = make_request(path="/api/admin/playground/anthropic/v1/models")
        with mock.patch(
            "src.anthropic_router.require_session_user",
            side_effect=RuntimeError("session store"),
        ):
            with self.assertRaises(Exception) as raised:
                require_anthropic_session_user(request)
        self.assertEqual(raised.exception.status_code, 500)

        user = AuthenticatedUser(username="admin", source="users_file")
        with mock.patch(
            "src.anthropic_router.models_manager.get_available_models",
            new=mock.AsyncMock(return_value=["glm"]),
        ) as get_models:
            from src.anthropic_router import _available_models

            self.assertEqual(await _available_models(user), ["glm"])
        get_models.assert_awaited_once_with(user)

        upstream = UpstreamAPIError(
            502,
            "failed",
            "upstream_error",
            upstream_status_code=529,
        )
        response = await upstream_api_error_handler(
            make_request(path="/anthropic/v1/messages"),
            upstream,
        )
        self.assertEqual(response.status_code, 529)

    async def test_available_models_uses_configured_fallback_at_discovery_deadline(self):
        user = AuthenticatedUser(username="admin", source="users_file")

        async def wait_forever(_user):
            await asyncio.Event().wait()

        with (
            mock.patch(
                "src.anthropic_router.models_manager.get_available_models",
                side_effect=wait_forever,
            ),
            mock.patch("src.anthropic_router.ANTHROPIC_MODEL_DISCOVERY_TIMEOUT_SECONDS", 0),
            mock.patch(
                "src.anthropic_router.get_configured_models",
                return_value=["configured-model"],
            ) as configured,
        ):
            from src.anthropic_router import _available_models

            self.assertEqual(await _available_models(user), ["configured-model"])
        configured.assert_called_once_with(user)

    @mock.patch("src.anthropic_router.create_usage_stats_context", return_value=None)
    async def test_error_paths_without_optional_stats_context(self, _create_stats):
        invalid_json = await self._request(
            "POST",
            "/anthropic/v1/messages",
            headers={**self._x_api_key(), "Content-Type": "application/json"},
            content=b"{",
        )
        self.assertEqual(invalid_json.status_code, 400)

        invalid_body = await self._request(
            "POST",
            "/anthropic/v1/messages",
            headers=self._x_api_key(),
            json={"model": "glm", "max_tokens": True, "messages": []},
        )
        self.assertEqual(invalid_body.status_code, 400)

        valid = {
            "model": "glm",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
        }
        with mock.patch(
            "src.anthropic_router.execute_codebuddy_chat",
            new=mock.AsyncMock(side_effect=CodeBuddyCredentialError("none")),
        ):
            credential = await self._request(
                "POST", "/anthropic/v1/messages", headers=self._x_api_key(), json=valid
            )
        self.assertEqual(credential.status_code, 500)

        with mock.patch(
            "src.anthropic_router.execute_codebuddy_chat",
            new=mock.AsyncMock(side_effect=RuntimeError("unexpected")),
        ):
            internal = await self._request(
                "POST", "/anthropic/v1/messages", headers=self._x_api_key(), json=valid
            )
        self.assertEqual(internal.status_code, 500)


if __name__ == "__main__":
    unittest.main()
