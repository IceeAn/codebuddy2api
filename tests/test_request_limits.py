import unittest

import httpx
from fastapi import FastAPI, Request

from src.request_limits import RequestBodyLimitMiddleware


class RequestBodyLimitMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def _app(self, global_limit=8, login_limit=4):
        app = FastAPI()

        @app.post("/{path:path}")
        async def echo(path: str, request: Request):
            return {"path": path, "body": (await request.body()).decode("ascii")}

        app.add_middleware(
            RequestBodyLimitMiddleware,
            max_body_bytes=global_limit,
            login_max_body_bytes=login_limit,
        )
        return app

    async def _request(self, app, path, **kwargs):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.post(path, **kwargs)

    async def test_declared_oversize_is_rejected_before_body_is_read(self):
        response = await self._request(self._app(), "/echo", content=b"123456789")

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"detail": "请求体超过允许上限"})
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_exact_global_limit_is_accepted(self):
        response = await self._request(self._app(), "/echo", content=b"12345678")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["body"], "12345678")

    async def test_login_path_and_slash_variant_use_smaller_limit(self):
        for path in ("/auth/login", "/auth/login/"):
            with self.subTest(path=path):
                response = await self._request(self._app(), path, content=b"12345")
                self.assertEqual(response.status_code, 413)

    async def test_chunked_body_is_counted_without_prebuffering(self):
        chunks_sent = []

        async def chunks():
            for chunk in (b"123", b"456", b"789"):
                chunks_sent.append(chunk)
                yield chunk

        response = await self._request(self._app(), "/echo", content=chunks())

        self.assertEqual(response.status_code, 413)
        self.assertEqual(chunks_sent, [b"123", b"456", b"789"])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_malformed_content_length_returns_400(self):
        middleware = RequestBodyLimitMiddleware(
            self._app(),
            max_body_bytes=8,
            login_max_body_bytes=4,
        )
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/echo",
                "headers": [(b"content-length", b"invalid")],
            },
            receive,
            send,
        )

        self.assertEqual(sent[0]["status"], 400)

    async def test_anthropic_declared_and_streamed_errors_use_protocol_envelope(self):
        declared = await self._request(
            self._app(),
            "/anthropic/v1/messages",
            content=b"123456789",
        )
        self.assertEqual(declared.status_code, 413)
        self.assertEqual(declared.json()["error"]["type"], "request_too_large")
        self.assertEqual(declared.json()["request_id"], declared.headers["request-id"])

        async def chunks():
            for chunk in (b"123", b"456", b"789"):
                yield chunk

        streamed = await self._request(
            self._app(),
            "/api/admin/playground/anthropic/v1/messages",
            content=chunks(),
        )
        self.assertEqual(streamed.status_code, 413)
        self.assertEqual(streamed.json()["error"]["type"], "request_too_large")
        self.assertEqual(streamed.json()["request_id"], streamed.headers["request-id"])

    async def test_anthropic_malformed_content_length_reuses_existing_request_id(self):
        middleware = RequestBodyLimitMiddleware(
            self._app(),
            max_body_bytes=8,
            login_max_body_bytes=4,
        )
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/anthropic/v1/messages",
                "state": {"anthropic_request_id": "req_existing"},
                "headers": [(b"content-length", b"invalid")],
            },
            receive,
            send,
        )
        self.assertEqual(sent[0]["status"], 400)
        self.assertIn((b"request-id", b"req_existing"), sent[0]["headers"])

    async def test_non_http_scope_passes_through(self):
        scopes = []

        async def downstream(scope, _receive, _send):
            scopes.append(scope)

        middleware = RequestBodyLimitMiddleware(
            downstream,
            max_body_bytes=8,
            login_max_body_bytes=4,
        )

        async def receive():
            return {"type": "lifespan.startup"}

        async def send(_message):
            return None

        scope = {"type": "lifespan"}
        await middleware(scope, receive, send)
        self.assertEqual(scopes, [scope])


if __name__ == "__main__":
    unittest.main()
