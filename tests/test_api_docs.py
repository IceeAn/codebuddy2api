import unittest

import httpx

import config
from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.session_store import session_store
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class ApiDocumentationTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.session_id = session_store.create("admin")
        self.api_key = api_key_store.create_key("admin", "docs-test")["api_key"]

    async def _request(self, path, *, session=False, api_key=False):
        headers = {}
        if session:
            headers["Cookie"] = f"{SESSION_COOKIE_NAME}={self.session_id}"
        if api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.get(path, headers=headers)

    async def test_documentation_endpoints_require_session_cookie(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                response = await self._request(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_api_key_cannot_replace_documentation_session(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(path=path):
                response = await self._request(path, api_key=True)
                self.assertEqual(response.status_code, 401)

    async def test_session_can_open_documentation_and_schema(self):
        docs = await self._request("/docs", session=True)
        redoc = await self._request("/redoc", session=True)
        schema = await self._request("/openapi.json", session=True)

        self.assertEqual(docs.status_code, 200)
        self.assertIn("url: '/openapi.json'", docs.text)
        self.assertEqual(redoc.status_code, 200)
        self.assertIn('spec-url="/openapi.json"', redoc.text)
        self.assertEqual(schema.status_code, 200)
        self.assertEqual(schema.json()["info"]["title"], "CodeBuddy2API")
        for response in (docs, redoc, schema):
            with self.subTest(content_type=response.headers["Content-Type"]):
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_schema_describes_external_api_authentication_and_chat_body(self):
        schema = (await self._request("/openapi.json", session=True)).json()

        self.assertEqual(
            schema["components"]["securitySchemes"]["ApiKeyBearer"],
            {"type": "http", "scheme": "bearer", "bearerFormat": "sk-..."},
        )
        self.assertEqual(
            schema["components"]["securitySchemes"]["SessionCookie"],
            {"type": "apiKey", "in": "cookie", "name": SESSION_COOKIE_NAME},
        )
        for path in ("/openai/v1/models", "/openai/v1/chat/completions"):
            method = "get" if path.endswith("models") else "post"
            self.assertEqual(schema["paths"][path][method]["security"], [{"ApiKeyBearer": []}])

        session_operations = (
            ("/auth/session", "get"),
            ("/codebuddy/auth/start", "post"),
            ("/codebuddy/auth/poll", "post"),
            ("/codebuddy/auth/cancel", "post"),
            ("/api/admin/status", "get"),
            ("/api/admin/api-keys", "get"),
            ("/api/admin/api-keys", "post"),
            ("/api/admin/api-keys/{key_id}", "delete"),
            ("/api/admin/credentials", "get"),
            ("/api/admin/credentials", "post"),
            ("/api/admin/credentials/{credential_id}/select", "post"),
            ("/api/admin/credentials/{credential_id}", "delete"),
            ("/api/admin/credentials/rotation/toggle", "post"),
            ("/api/admin/credentials/{credential_id}/test", "post"),
            ("/api/admin/settings", "get"),
            ("/api/admin/settings", "put"),
        )
        for path, method in session_operations:
            with self.subTest(path=path, method=method):
                self.assertEqual(
                    schema["paths"][path][method]["security"],
                    [{"SessionCookie": []}],
                )

        request_schema = schema["paths"]["/openai/v1/chat/completions"]["post"][
            "requestBody"
        ]["content"]["application/json"]["schema"]
        self.assertEqual(request_schema["type"], "object")
        self.assertEqual(request_schema["required"], ["messages"])
        self.assertTrue(request_schema["additionalProperties"])
        messages_schema = request_schema["properties"]["messages"]
        self.assertEqual(messages_schema["minItems"], 1)
        self.assertEqual(messages_schema["items"]["required"], ["role"])
        self.assertEqual(
            messages_schema["items"]["anyOf"],
            [
                {"required": ["content"]},
                {
                    "required": ["tool_calls"],
                    "properties": {"role": {"const": "assistant"}},
                },
            ],
        )
        self.assertEqual(
            messages_schema["items"]["properties"]["tool_calls"],
            {"type": "array", "minItems": 1, "items": {"type": "object"}},
        )

        credential_create_schema = schema["components"]["schemas"]["CredentialCreateRequest"]
        self.assertEqual(set(credential_create_schema["properties"]), {"bearer_token"})
        token_schema = credential_create_schema["properties"]["bearer_token"]
        self.assertEqual(token_schema["minLength"], 1)
        self.assertEqual(token_schema["pattern"], "\\S")
        self.assertTrue(
            {
                "model",
                "messages",
                "stream",
                "temperature",
                "max_tokens",
                "tools",
                "tool_choice",
                "reasoning_effort",
                "thinking",
                "enable_thinking",
            }.issubset(request_schema["properties"])
        )

    async def test_schema_contains_public_routes_and_hides_internal_routes(self):
        paths = (await self._request("/openapi.json", session=True)).json()["paths"]

        self.assertIn("/openai/v1/models", paths)
        self.assertIn("/auth/login", paths)
        self.assertIn("/api/admin/status", paths)
        self.assertIn("/codebuddy/auth/start", paths)
        self.assertIn("/codebuddy/auth/cancel", paths)
        self.assertIn("/health", paths)
        self.assertNotIn("/api/admin/playground/openai/v1/models", paths)
        self.assertNotIn("/api/admin/playground/openai/v1/chat/completions", paths)
        self.assertNotIn("/", paths)
        self.assertNotIn("/docs", paths)
        self.assertNotIn("/redoc", paths)
        self.assertNotIn("/openapi.json", paths)


if __name__ == "__main__":
    unittest.main()
