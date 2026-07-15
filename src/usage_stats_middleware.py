"""在最终 ASGI 响应完成后收口脱敏请求统计。"""

import asyncio
import inspect
import logging
import threading
from collections import defaultdict
from typing import DefaultDict

from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

USAGE_STATS_CONTEXT_STATE_KEY = "usage_stats_context"


class DroppedCompletionEvents:
    """记录当前进程因统计完成阶段异常而丢失的请求数。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counts: DefaultDict[str, int] = defaultdict(int)

    def record(self, username: str) -> None:
        with self._lock:
            self._counts[str(username or "")] += 1

    def get(self, username: str) -> int:
        with self._lock:
            return self._counts.get(str(username or ""), 0)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._counts.clear()


dropped_completion_events = DroppedCompletionEvents()


class UsageStatsMiddleware:
    """观察最终状态和已发送字节，并以不影响主请求的方式完成统计。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        http_status = None
        response_bytes = 0
        client_disconnected = False
        response_completed = False

        async def receive_wrapper() -> Message:
            nonlocal client_disconnected
            message = await receive()
            if message["type"] == "http.disconnect" and not response_completed:
                client_disconnected = True
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal http_status, response_bytes, client_disconnected, response_completed
            try:
                await send(message)
            except OSError:
                client_disconnected = True
                raise
            if message["type"] == "http.response.start":
                http_status = message["status"]
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                if not message.get("more_body", False):
                    response_completed = True

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except (ClientDisconnect, asyncio.CancelledError, OSError):
            if not response_completed:
                client_disconnected = True
            raise
        finally:
            context = state.get(USAGE_STATS_CONTEXT_STATE_KEY)
            if context is not None:
                try:
                    complete_response = context.complete_response
                    completion_kwargs = {
                        "http_status": http_status,
                        "response_bytes": response_bytes,
                        "client_disconnected": client_disconnected,
                    }
                    if inspect.iscoroutinefunction(complete_response):
                        await complete_response(**completion_kwargs)
                    else:
                        result = await asyncio.to_thread(
                            complete_response,
                            **completion_kwargs,
                        )
                        if inspect.isawaitable(result):
                            await result
                except Exception:
                    username = str(getattr(context, "username", "") or "")
                    dropped_completion_events.record(username)
                    logger.exception("持久化请求统计失败，已保留原始响应")
