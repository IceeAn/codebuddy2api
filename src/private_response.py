"""为包含私有数据的最终 HTTP 响应统一设置缓存策略。"""

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .usage_stats_middleware import UsageStatsMiddleware
from .auth_types import SESSION_COOKIE_NAME, SESSION_REFRESH_STATE_KEY, SESSION_TTL_SECONDS
from .session_store import session_store

PRIVATE_NO_STORE_VALUE = "private, no-store"
_PRIVATE_NO_STORE_STATE_KEY = "private_no_store"


def _is_secure_scope(scope: Scope) -> bool:
    return scope.get("scheme") == "https"


def _session_id_from_scope(scope: Scope) -> str:
    cookie_header = Headers(scope=scope).get("Cookie", "")
    for part in cookie_header.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name == SESSION_COOKIE_NAME:
            return value
    return ""


def _session_cookie_header(session_id: str, secure: bool) -> str:
    response = Response()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return response.headers["Set-Cookie"]


class PrivateNoStoreRoute(APIRoute):
    """标记最终响应不得缓存的 HTTP 路由。"""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if match != Match.NONE:
            scope.setdefault("state", {})[_PRIVATE_NO_STORE_STATE_KEY] = True
        return match, child_scope


class PrivateNoStoreMiddleware:
    """在最终响应头写出前应用私有响应缓存策略。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Starlette 的斜杠重定向使用 scope 浅拷贝；预先创建共享 state，
        # 使重定向目标的路由匹配也能标记原请求。
        private_response_state = scope.setdefault("state", {})

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                if private_response_state.get(_PRIVATE_NO_STORE_STATE_KEY):
                    response_headers["Cache-Control"] = PRIVATE_NO_STORE_VALUE

                refresh = private_response_state.get(SESSION_REFRESH_STATE_KEY)
                status = int(message.get("status", 0))
                if (
                    refresh is None
                    and 300 <= status < 400
                    and private_response_state.get(_PRIVATE_NO_STORE_STATE_KEY)
                ):
                    redirect_session_id = _session_id_from_scope(scope)
                    if redirect_session_id and session_store.get_user(redirect_session_id):
                        refresh = {
                            "session_id": redirect_session_id,
                            "secure": _is_secure_scope(scope),
                        }
                if refresh is not None:
                    response_headers.append(
                        "Set-Cookie",
                        _session_cookie_header(
                            refresh["session_id"],
                            bool(refresh["secure"]),
                        ),
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)


class PrivateNoStoreFastAPI(FastAPI):
    """确保私有缓存策略包裹 FastAPI 的完整错误处理中间件栈。"""

    def build_middleware_stack(self) -> ASGIApp:
        return PrivateNoStoreMiddleware(UsageStatsMiddleware(super().build_middleware_stack()))
