"""预检请求体声明长度，并限制应用实际读取的字节数。"""

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
    async def _send_error(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": detail},
            headers={"Cache-Control": PRIVATE_NO_STORE_VALUE},
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

        async def receive_wrapper() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=REQUEST_BODY_TOO_LARGE_DETAIL,
                    )
            return message

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start" and message.get("status") == 413:
                MutableHeaders(scope=message)["Cache-Control"] = PRIVATE_NO_STORE_VALUE
            await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)
