"""为包含私有数据的最终 HTTP 响应统一设置缓存策略。"""

from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.datastructures import MutableHeaders
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .usage_stats_middleware import UsageStatsMiddleware

PRIVATE_NO_STORE_VALUE = "private, no-store"
_PRIVATE_NO_STORE_STATE_KEY = "private_no_store"


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
            if (
                message["type"] == "http.response.start"
                and private_response_state.get(_PRIVATE_NO_STORE_STATE_KEY)
            ):
                MutableHeaders(scope=message)["Cache-Control"] = PRIVATE_NO_STORE_VALUE
            await send(message)

        await self.app(scope, receive, send_wrapper)


class PrivateNoStoreFastAPI(FastAPI):
    """确保私有缓存策略包裹 FastAPI 的完整错误处理中间件栈。"""

    def build_middleware_stack(self) -> ASGIApp:
        return PrivateNoStoreMiddleware(UsageStatsMiddleware(super().build_middleware_stack()))
