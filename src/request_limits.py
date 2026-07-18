"""预检请求体声明长度，并限制应用实际读取的字节数。"""

import json
import uuid

from fastapi import HTTPException
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

PRIVATE_NO_STORE_VALUE = "private, no-store"
REQUEST_BODY_TOO_LARGE_DETAIL = "请求体超过允许上限"


class RequestBodyLimitMiddleware:
    """检查声明长度并累计下游读取字节，不主动排空或预缓存请求体。"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        login_max_body_bytes: int,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.login_max_body_bytes = login_max_body_bytes

    def _limit_for_scope(self, scope: Scope) -> int:
        if scope.get("path") in {"/auth/login", "/auth/login/"}:
            return min(self.max_body_bytes, self.login_max_body_bytes)
        return self.max_body_bytes

    @staticmethod
    def _declared_length(scope: Scope) -> int | None:
        raw_value = Headers(scope=scope).get("Content-Length")
        if raw_value is None:
            return None
        value = raw_value.strip()
        if not value.isascii() or not value.isdigit():
            raise ValueError("Invalid Content-Length")
        return int(value)

    @staticmethod
    def _is_anthropic_scope(scope: Scope) -> bool:
        path = scope.get("path", "")
        return path.startswith(("/anthropic/", "/api/admin/playground/anthropic/"))

    @staticmethod
    def _anthropic_request_id(scope: Scope) -> str:
        state = scope.setdefault("state", {})
        request_id = state.get("anthropic_request_id")
        if not isinstance(request_id, str):
            request_id = f"req_{uuid.uuid4().hex}"
            state["anthropic_request_id"] = request_id
        return request_id

    @classmethod
    def _error_content(cls, scope: Scope, status_code: int, detail: str) -> tuple[dict, dict]:
        if not cls._is_anthropic_scope(scope):
            return {"detail": detail}, {"Cache-Control": PRIVATE_NO_STORE_VALUE}
        request_id = cls._anthropic_request_id(scope)
        error_type = "request_too_large" if status_code == 413 else "invalid_request_error"
        return {
            "type": "error",
            "error": {"type": error_type, "message": detail},
            "request_id": request_id,
        }, {
            "Cache-Control": PRIVATE_NO_STORE_VALUE,
            "request-id": request_id,
        }

    @classmethod
    async def _send_error(
        cls,
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
    ) -> None:
        content, headers = cls._error_content(scope, status_code, detail)
        response = JSONResponse(
            status_code=status_code,
            content=content,
            headers=headers,
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self._limit_for_scope(scope)
        try:
            declared_length = self._declared_length(scope)
        except ValueError:
            await self._send_error(scope, receive, send, 400, "无效的 Content-Length")
            return
        if declared_length is not None and declared_length > limit:
            await self._send_error(
                scope,
                receive,
                send,
                413,
                REQUEST_BODY_TOO_LARGE_DETAIL,
            )
            return

        received_bytes = 0
        streamed_limit_exceeded = False

        async def receive_wrapper() -> Message:
            nonlocal received_bytes, streamed_limit_exceeded
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit:
                    streamed_limit_exceeded = True
                    raise HTTPException(
                        status_code=413,
                        detail=REQUEST_BODY_TOO_LARGE_DETAIL,
                    )
            return message

        replacing_anthropic_error = False

        async def send_wrapper(message: Message) -> None:
            nonlocal replacing_anthropic_error
            if (
                streamed_limit_exceeded
                and self._is_anthropic_scope(scope)
                and message["type"] == "http.response.start"
                and message.get("status") == 413
            ):
                replacing_anthropic_error = True
                content, headers = self._error_content(
                    scope,
                    413,
                    REQUEST_BODY_TOO_LARGE_DETAIL,
                )
                encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                response_headers = MutableHeaders(scope=message)
                response_headers["Content-Type"] = "application/json"
                response_headers["Content-Length"] = str(len(encoded))
                for name, value in headers.items():
                    response_headers[name] = value
            if message["type"] == "http.response.start" and message.get("status") == 413:
                MutableHeaders(scope=message)["Cache-Control"] = PRIVATE_NO_STORE_VALUE
            if replacing_anthropic_error and message["type"] == "http.response.body":
                content, _headers = self._error_content(scope, 413, REQUEST_BODY_TOO_LARGE_DETAIL)
                message = {
                    "type": "http.response.body",
                    "body": json.dumps(
                        content,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    "more_body": False,
                }
                replacing_anthropic_error = False
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)
