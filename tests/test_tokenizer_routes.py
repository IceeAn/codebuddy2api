"""外部 API 与会话 playground 计数隔离。"""

import tests  # 在生产模块导入前隔离测试数据目录。

import json
import unittest
from unittest import mock

import httpx
import config

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.session_store import session_store
from src.tokenizer_store import get_tokenizer_store
from tests.helpers import TempConfigMixin, configure_users_file
from tests.test_tokenizer import tokenizer_files
from web import app


class TokenizerRouteTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        from src.tokenizer_runtime import tokenizer_runtime

        tokenizer_runtime.startup()
        self.addCleanup(tokenizer_runtime.shutdown)
        configure_users_file(self.temp_path)
        self.key = api_key_store.create_key("admin", "计数")["api_key"]
        self.cookie = session_store.create("admin")
        store = get_tokenizer_store()
        resource = store.create(
            "admin", "词表", tokenizer_files(), {"encoder": "auto", "format": "hf"}
        )
        store.save_mappings("admin", {"custom": resource["id"]})

    async def request(self, path, headers, data):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost",
            headers={"Origin": "http://localhost"}
        ) as client:
            return await client.post(path, headers=headers, json=data)

    async def test_text_counter_and_cookie_boundary(self):
        response = await self.request(
            "/tokenizer/v1/count_tokens",
            {"Authorization": "Bearer " + self.key},
            {"model": "custom", "text": "hello world"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"input_tokens": 2})
        self.assertEqual(response.headers["x-tokenizer-method"], "text")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        denied = await self.request(
            "/tokenizer/v1/count_tokens",
            {"Cookie": f"{SESSION_COOKIE_NAME}={self.cookie}"},
            {"model": "custom", "text": "hello"},
        )
        self.assertEqual(denied.status_code, 401)

    async def test_anthropic_count_works_without_credentials_or_max_tokens(self):
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        response = await self.request(
            "/anthropic/v1/messages/count_tokens",
            headers,
            {
                "model": "anthropic/codebuddy/custom",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(response.json()["input_tokens"], 1)
        self.assertEqual(response.headers["x-tokenizer-method"], "budget_v1")
        self.assertTrue(response.headers["request-id"].startswith("req_"))

    async def test_playground_requires_cookie(self):
        path = "/api/admin/playground/tokenizer/v1/count_tokens"
        body = {"model": "custom", "text": "hello"}
        denied = await self.request(path, {"Authorization": "Bearer " + self.key}, body)
        self.assertEqual(denied.status_code, 401)
        response = await self.request(
            path, {"Cookie": f"{SESSION_COOKIE_NAME}={self.cookie}"}, body
        )
        self.assertEqual(response.json(), {"input_tokens": 1})

    async def test_visualization_requires_session_and_returns_byte_offsets(self):
        path = '/api/admin/tokenizers/encode'
        body = {'model': 'custom', 'text': 'hello  你好'}
        denied = await self.request(path, {'Authorization': 'Bearer ' + self.key}, body)
        self.assertEqual(denied.status_code, 401)
        headers = {'Cookie': f'{SESSION_COOKIE_NAME}={self.cookie}'}
        response = await self.request(path, headers, body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            'text': 'hello  你好', 'input_tokens': 2,
            'tokens': [{'id': 1, 'start': 0, 'end': 5}, {'id': 3, 'start': 7, 'end': 13}],
        })
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual(response.headers['x-tokenizer-method'], 'text')
        missing = await self.request(path, headers, {'model': 'missing', 'text': 'hello'})
        self.assertEqual(missing.status_code, 404)

    async def test_visualization_input_limit_allows_boundary_and_rejects_before_dispatch(self):
        headers = {'Cookie': f'{SESSION_COOKIE_NAME}={self.cookie}'}
        maximum = 256 * 1024
        for text in (' ' * maximum, ' ' * (maximum - 3) + '你'):
            with self.subTest(bytes=len(text.encode('utf-8'))):
                response = await self.request(
                    '/api/admin/tokenizers/encode', headers, {'model': 'custom', 'text': text}
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['text'], text)

        for path in ('/api/admin/tokenizers/encode', '/api/admin/tokenizers/encode/'):
            with self.subTest(path=path), mock.patch(
                'src.tokenizer_service.tokenizer_runtime.execute'
            ) as execute, mock.patch('src.tokenizer_service.get_tokenizer_store') as store:
                # 末尾汉字使输入仅比上限多 1 字节，避免误按字符数判断。
                response = await self.request(
                    path, headers,
                    {'model': 'custom', 'text': ' ' * (maximum - 2) + '你'},
                )
                if response.status_code == 307:
                    response = await self.request(
                        response.headers['location'], headers,
                        {'model': 'custom', 'text': ' ' * (maximum - 2) + '你'},
                    )
                self.assertEqual(response.status_code, 413, response.text)
                self.assertIn('可视化', response.json()['detail'])
                self.assertEqual(response.headers['cache-control'], 'private, no-store')
                execute.assert_not_called()
                store.assert_not_called()

    async def test_visualization_limit_is_configurable_and_only_applies_to_visualization(self):
        headers = {'Cookie': f'{SESSION_COOKIE_NAME}={self.cookie}'}
        with mock.patch.dict(
            config._config_cache, {'CODEBUDDY_TOKENIZER_VISUALIZE_MAX_BYTES': '6'}
        ):
            for text, status in (('', 200), ('你好', 200), ('你好a', 413), ('你好🙂', 413)):
                with self.subTest(text=text):
                    response = await self.request(
                        '/api/admin/tokenizers/encode', headers, {'model': 'custom', 'text': text}
                    )
                    self.assertEqual(response.status_code, status, response.text)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://localhost',
                headers={'Origin': 'http://localhost'},
            ) as client:
                # JSON Unicode 转义增加传输体积，但不改变输入文本的 UTF-8 字节数。
                response = await client.post(
                    '/api/admin/tokenizers/encode',
                    headers={**headers, 'Content-Type': 'application/json'},
                    content=json.dumps({'model': 'custom', 'text': '你好'}, ensure_ascii=True),
                )
            self.assertEqual(response.status_code, 200, response.text)
            for path, auth, body in (
                ('/tokenizer/v1/count_tokens', {'Authorization': 'Bearer ' + self.key},
                 {'model': 'custom', 'text': 'hello world'}),
                ('/api/admin/playground/tokenizer/v1/count_tokens', headers,
                 {'model': 'custom', 'text': 'hello world'}),
                ('/anthropic/v1/messages/count_tokens',
                 {'x-api-key': self.key, 'anthropic-version': '2023-06-01'},
                 {'model': 'custom', 'messages': [{'role': 'user', 'content': 'hello world'}]}),
            ):
                with self.subTest(path=path):
                    response = await self.request(path, auth, body)
                    self.assertEqual(response.status_code, 200, response.text)

        with mock.patch.dict(
            config._config_cache, {'CODEBUDDY_TOKENIZER_VISUALIZE_MAX_BYTES': '300000'}
        ):
            response = await self.request(
                '/api/admin/tokenizers/encode', headers,
                {'model': 'custom', 'text': ' ' * (256 * 1024 + 1)},
            )
            self.assertEqual(response.status_code, 200, response.text)

    async def test_admin_resource_lifecycle_and_errors(self):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost",
            headers={"Cookie": f"{SESSION_COOKIE_NAME}={self.cookie}", "Origin": "http://localhost"},
        ) as client:
            response = await client.get("/api/admin/tokenizers")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["limits"]["upload_max_bytes"], 67108864)
            self.assertEqual(response.json()["limits"]["visualize_max_bytes"], 262144)
            uploaded = await client.post(
                "/api/admin/tokenizers/resources",
                data={"name": "用户词表"},
                files=[
                    (
                        "files",
                        (
                            "tokenizer.json",
                            tokenizer_files()["tokenizer.json"],
                            "application/json",
                        ),
                    )
                ],
            )
            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            self.assertFalse(uploaded.json()["update_available"])
            resource_id = uploaded.json()["id"]
            mapped = await client.put(
                "/api/admin/tokenizers/mappings",
                json={"mappings": {"custom": resource_id}},
            )
            self.assertEqual(mapped.status_code, 200)
            self.assertEqual(
                (
                    await client.delete(
                        "/api/admin/tokenizers/resources/" + resource_id
                    )
                ).status_code,
                409,
            )
            self.assertEqual(
                (
                    await client.put(
                        "/api/admin/tokenizers/mappings", json={"mappings": {}}
                    )
                ).status_code,
                200,
            )
            self.assertEqual(
                (
                    await client.delete(
                        "/api/admin/tokenizers/resources/" + resource_id
                    )
                ).status_code,
                200,
            )
            self.assertEqual(
                (
                    await client.post("/api/admin/tokenizers/builtin/missing/snapshot")
                ).status_code,
                404,
            )
            with mock.patch("src.tokenizer_router.get_tokenizer_store") as store:
                store.return_value.snapshot_builtin.return_value = {"id": "snapshot"}
                self.assertEqual(
                    (
                        await client.post(
                            "/api/admin/tokenizers/builtin/known/snapshot"
                        )
                    ).status_code,
                    201,
                )
            for files in (
                [("files", ("bad.py", b"x"))],
                [
                    ("files", ("tokenizer.json", b"{}")),
                    ("files", ("tokenizer.json", b"{}")),
                ],
                [("other", ("tokenizer.json", b"{}"))],
            ):
                result = await client.post(
                    "/api/admin/tokenizers/resources",
                    data={"name": "错误"},
                    files=files,
                )
                self.assertEqual(result.status_code, 400, result.text)
            self.assertEqual(
                (
                    await client.post(
                        "/api/admin/tokenizers/resources", data={"name": "空"}
                    )
                ).status_code,
                400,
            )
            for body in ("{", "[]", "{}", '{"mappings":[]}'):
                self.assertEqual(
                    (
                        await client.put("/api/admin/tokenizers/mappings", content=body)
                    ).status_code,
                    400,
                )
            denied = await client.get(
                "/api/admin/tokenizers",
                headers={"Cookie": "", "Authorization": "Bearer " + self.key},
            )
            self.assertEqual(denied.status_code, 401)

    async def test_anthropic_count_errors_and_playground(self):
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        body = {"model": "custom", "messages": [{"role": "user", "content": "hello"}]}
        for status, kind in (
            (404, "not_found_error"),
            (413, "request_too_large"),
            (429, "rate_limit_error"),
            (503, "api_error"),
            (400, "invalid_request_error"),
        ):
            from src.tokenizer_engine import TokenizerError

            with mock.patch(
                "src.anthropic_router.count_tokens",
                side_effect=TokenizerError("受控错误", status),
            ):
                result = await self.request(
                    "/anthropic/v1/messages/count_tokens", headers, body
                )
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.json()["error"]["type"], kind)
        self.assertEqual(
            (
                await self.request("/anthropic/v1/messages/count_tokens", headers, {})
            ).status_code,
            400,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost",
            headers={"Origin": "http://localhost"}
        ) as client:
            result = await client.post(
                "/anthropic/v1/messages/count_tokens", headers=headers, content="{"
            )
            self.assertEqual(result.status_code, 400)
            response = await client.post(
                "/api/admin/playground/anthropic/v1/messages/count_tokens",
                headers={
                    "Cookie": f"{SESSION_COOKIE_NAME}={self.cookie}",
                    "anthropic-version": "2023-06-01",
                },
                json=body,
            )
            self.assertEqual(response.status_code, 200, response.text)
        denied = await self.request(
            "/api/admin/playground/anthropic/v1/messages/count_tokens", headers, body
        )
        self.assertEqual(denied.status_code, 401)

    async def test_service_validation_and_namespace_policy(self):
        from src.tokenizer_service import count_tokens
        from src.tokenizer_engine import TokenizerError
        from src.auth_types import AuthenticatedUser
        import config

        user = AuthenticatedUser("admin", "session")
        for body in ([], {}, {"model": "x", "text": 42}):
            with self.assertRaises(TokenizerError):
                await count_tokens(body, user)
        with mock.patch.object(config, "get_strip_model_namespace", return_value=False):
            response = await count_tokens({"model": "custom", "text": "hello"}, user)
            self.assertEqual(response[0]["input_tokens"], 1)
        with mock.patch.object(config, "get_strip_model_namespace", return_value=True):
            response = await count_tokens(
                {"model": "vendor/custom", "text": "hello"}, user
            )
            self.assertEqual(response[0]["input_tokens"], 1)

    async def test_pure_text_unknown_model_returns_not_found(self):
        response = await self.request(
            "/tokenizer/v1/count_tokens",
            {"Authorization": "Bearer " + self.key},
            {"model": "missing", "text": "hello"},
        )
        self.assertEqual(response.status_code, 404)

    async def test_official_models_count_translated_thinking_modes_and_history(self):
        from src.anthropic_compat import anthropic_thinking_signature

        path = "/anthropic/v1/messages/count_tokens"
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        counts = {}
        for mode in ("disabled", "adaptive", "enabled"):
            thinking = {"type": mode}
            if mode == "enabled":
                thinking["budget_tokens"] = 1024
            result = await self.request(
                path,
                headers,
                {
                    "model": "minimax-m3",
                    "thinking": thinking,
                    "messages": [{"role": "user", "content": "你好"}],
                },
            )
            self.assertEqual(result.status_code, 200, result.text)
            counts[mode] = result.json()["input_tokens"]
        self.assertNotEqual(counts["disabled"], counts["enabled"])
        self.assertEqual(counts["adaptive"], counts["enabled"])
        with mock.patch(
            "config.get_forced_reasoning_models", return_value=["minimax-m3"]
        ):
            forced = await self.request(
                path,
                headers,
                {
                    "model": "minimax-m3",
                    "thinking": {"type": "disabled"},
                    "messages": [{"role": "user", "content": "你好"}],
                },
            )
        self.assertEqual(forced.json()["input_tokens"], counts["enabled"])

        history = "历史推理测试内容"
        messages = [
            {"role": "user", "content": "第一问"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": history,
                        "signature": anthropic_thinking_signature(history),
                    },
                    {"type": "text", "text": "第一答"},
                ],
            },
            {"role": "user", "content": "第二问"},
        ]
        for clear in (True, False):
            with mock.patch(
                "config.get_forced_reasoning_models", return_value=["kimi-k2.6"]
            ):
                result = await self.request(
                    path,
                    headers,
                    {
                        "model": "kimi-k2.6",
                        "messages": messages,
                        "thinking": {
                            "type": "enabled",
                            "budget_tokens": 1024,
                            "clear_thinking": clear,
                        },
                    },
                )
            self.assertEqual(result.status_code, 200, result.text)
            counts[clear] = result.json()["input_tokens"]
        self.assertGreater(counts[False], counts[True])
