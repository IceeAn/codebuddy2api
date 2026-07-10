"""CodeBuddy 上游流式调用服务。"""
import asyncio
import copy
import json
import logging
import random
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Literal, Optional, Protocol

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect

from .openai_compat import (
    CodeBuddyResponseEvent,
    CompletionResponseContext,
    OpenAIStreamNormalizer,
    ToolCallIndexState,
    add_openai_tool_call_indexes,
    normalize_openai_stream_chunk_envelope,
)
from .sse import (
    SSE_DONE,
    SSEDataError,
    SSE_HEADERS,
    format_sse_done,
    format_sse_error,
    format_sse_event,
    iter_sse_events,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamObservation:
    """上游请求观察器接收的脱敏信号。"""

    kind: Literal["retry", "upstream_event", "error", "client_disconnect"]
    retry_count: Optional[int] = None
    error_type: Optional[str] = None
    status_code: Optional[int] = None
    has_reasoning_content: bool = False
    has_content: bool = False
    tool_call_count: int = 0
    usage: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None
    upstream_model: Optional[str] = None
    upstream_done: bool = False


class StreamObserver(Protocol):
    """同步接收脱敏上游信号的可调用观察器。"""

    def __call__(self, observation: StreamObservation, /) -> None:
        ...


class UpstreamAPIError(HTTPException):
    """可由 OpenAI 兼容入口稳定序列化的上游错误。"""

    def __init__(
            self,
            status_code: int,
            message: str,
            error_type: str,
            *,
            code: Any = None,
            headers: Optional[Dict[str, str]] = None,
    ):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.error = {"message": message, "type": error_type}
        if code is not None:
            self.error["code"] = code


def _extract_error_fields(
        error_value: Any,
        fallback_message: str,
        fallback_type: str,
) -> tuple[str, str, Any]:
    """从常见上游错误对象中提取安全且稳定的 OpenAI 错误字段。"""
    candidate = error_value.get("error", error_value) if isinstance(error_value, dict) else error_value
    if isinstance(candidate, dict):
        message = candidate.get("message")
        error_type = candidate.get("type")
        code = candidate.get("code")
        return (
            message if isinstance(message, str) and message else fallback_message,
            error_type if isinstance(error_type, str) and error_type else fallback_type,
            code,
        )
    if isinstance(candidate, str) and candidate:
        return candidate, fallback_type, None
    return fallback_message, fallback_type, None


def get_codebuddy_api_url() -> str:
    """动态加载 CodeBuddy API URL，确保安全白名单即时生效。"""
    from config import get_codebuddy_api_endpoint

    return f"{get_codebuddy_api_endpoint()}/v2/chat/completions"


class SecurityConfig:
    """安全配置管理器。"""

    @staticmethod
    def get_ssl_verify() -> bool:
        """获取 SSL 验证设置，生产默认启用。"""
        from config import get_ssl_verify

        ssl_verify = get_ssl_verify()
        if not ssl_verify:
            logger.warning("SSL验证已禁用，仅应在受控调试环境使用。")
        return ssl_verify


HTTP_CLIENT_CONFIG = {
    "verify": SecurityConfig.get_ssl_verify(),
    "timeout": httpx.Timeout(300.0, connect=30.0, read=300.0),
    "limits": httpx.Limits(max_keepalive_connections=20, max_connections=100),
    "trust_env": False,
}

_http_client_pool: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """获取全局 HTTP 客户端池。"""
    global _http_client_pool
    if _http_client_pool is None:
        async with _client_lock:
            if _http_client_pool is None:
                _http_client_pool = httpx.AsyncClient(**HTTP_CLIENT_CONFIG)
    return _http_client_pool


async def close_http_client():
    """关闭全局 HTTP 客户端池。"""
    global _http_client_pool
    async with _client_lock:
        if _http_client_pool is not None:
            await _http_client_pool.aclose()
            _http_client_pool = None


class AppLifecycleManager:
    """应用生命周期管理器。"""

    @staticmethod
    async def startup():
        """应用启动时初始化连接池。"""
        logger.info("CodeBuddy Router 启动中...")
        await get_http_client()
        logger.info("HTTP 连接池已初始化")

    @staticmethod
    async def shutdown():
        """应用关闭时清理连接池。"""
        logger.info("CodeBuddy Router 关闭中...")
        await close_http_client()
        logger.info("资源清理完成")


lifecycle_manager = AppLifecycleManager()


class SSEConnectionManager:
    """仅重试确定发生在请求发送前的连接阶段错误。"""

    def __init__(
            self,
            max_connect_retries: int = 1,
            retry_delay: float = 0.25,
            jitter_ratio: float = 0.2,
            random_source: Callable[[], float] = random.random,
    ):
        if max_connect_retries < 0:
            raise ValueError("max_connect_retries must be non-negative")
        self.max_connect_retries = max_connect_retries
        self.retry_delay = retry_delay
        self.jitter_ratio = jitter_ratio
        self.random_source = random_source

    def _retry_wait(self, attempt: int) -> float:
        base_wait = self.retry_delay * (2 ** attempt)
        return base_wait * (1 + self.jitter_ratio * self.random_source())

    async def _wait_before_retry(self, attempt: int, error: Exception) -> None:
        wait_time = self._retry_wait(attempt)
        logger.warning(
            "CodeBuddy 连接失败，%.3f 秒后重试（第 %d 次）: %s",
            wait_time,
            attempt + 1,
            error,
        )
        await asyncio.sleep(wait_time)

    async def stream_with_retry(
            self,
            stream_func,
            *args,
            on_retry: Optional[Callable[[int], None]] = None,
            **kwargs,
    ):
        """流式请求只在连接错误且尚未输出下游 chunk 时重试。"""
        has_emitted_chunk = False
        for attempt in range(self.max_connect_retries + 1):  # pragma: no branch
            try:
                async with aclosing(stream_func(*args, **kwargs)) as active_stream:
                    async for chunk in active_stream:
                        has_emitted_chunk = True
                        yield chunk
                return
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                if has_emitted_chunk:
                    logger.error("流式响应已开始，连接错误后不重放请求: %s", e)
                    raise
                if attempt < self.max_connect_retries:
                    if on_retry is not None:
                        on_retry(attempt + 1)
                    await self._wait_before_retry(attempt, e)
                    continue
                logger.error("CodeBuddy 连接重试耗尽: %s", e)
                raise

    async def run_with_retry(
            self,
            operation: Callable[[], Any],
            on_retry: Optional[Callable[[int], None]] = None,
    ) -> Any:
        """为非流式聚合路径应用相同的连接阶段重试策略。"""
        for attempt in range(self.max_connect_retries + 1):  # pragma: no branch
            try:
                return await operation()
            except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                if attempt < self.max_connect_retries:
                    if on_retry is not None:
                        on_retry(attempt + 1)
                    await self._wait_before_retry(attempt, error)
                    continue
                logger.error("CodeBuddy 连接重试耗尽: %s", error)
                raise


async def _prepend_chunk(first_chunk: Any, remaining: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """把已预取的首块放回响应流。"""
    yield first_chunk
    async for chunk in remaining:
        yield chunk


class _ManagedStreamingResponse(StreamingResponse):
    """首块就绪后才发送响应头，并在等待及发送期间监听客户端断开。

    标准 StreamingResponse 会先发送 200 再迭代响应体，无法让首块前的
    上游异常进入 FastAPI 异常处理器；此类只接管首块与断连协调，实际
    响应体发送仍委托 Starlette，避免重复实现其编码和 ASGI 消息逻辑。
    """

    def __init__(self, content, close_callback, disconnect_callback, **kwargs):
        super().__init__(content, **kwargs)
        self._close_callback = close_callback
        self._disconnect_callback = disconnect_callback

    async def _stream_from_first_chunk(self, first_chunk: Any, send) -> None:
        self.body_iterator = _prepend_chunk(first_chunk, self.body_iterator)
        await super().stream_response(send)

    async def __call__(self, _scope, receive, send) -> None:
        first_chunk_task = asyncio.create_task(anext(self.body_iterator))
        disconnect_task = asyncio.create_task(self.listen_for_disconnect(receive))
        tasks = [first_chunk_task, disconnect_task]
        try:
            completed, _pending = await asyncio.wait(
                (first_chunk_task, disconnect_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in completed:
                await disconnect_task
                self._disconnect_callback()
                return

            first_chunk = await first_chunk_task

            async def stream_from_first_chunk():
                try:
                    await self._stream_from_first_chunk(first_chunk, send)
                except OSError as error:
                    raise ClientDisconnect() from error

            stream_task = asyncio.create_task(stream_from_first_chunk())
            tasks.append(stream_task)
            completed, _pending = await asyncio.wait(
                (stream_task, disconnect_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stream_task in completed:
                await stream_task
                return
            await disconnect_task
            self._disconnect_callback()
            return
        except ClientDisconnect:
            self._disconnect_callback()
            raise
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._close_callback()


class StreamResponseAggregator:
    """将 CodeBuddy SSE 事件聚合为 OpenAI 非流式响应。"""

    def __init__(self, response_context: CompletionResponseContext):
        self.response_context = response_context
        self.data = {
            "content": "",
            "reasoning_content": "",
            "finish_reason": None,
            "usage": None,
            "system_fingerprint": None,
        }
        self.tool_call_index_state = ToolCallIndexState()
        self.tool_call_map: Dict[int, Dict[str, Any]] = {}

    def process_event(self, event: CodeBuddyResponseEvent):
        """处理共享上游响应语义事件。"""
        obj = event.chunk_data
        self.data["system_fingerprint"] = obj.get("system_fingerprint") or self.data["system_fingerprint"]

        if event.usage:
            self.data["usage"] = event.usage

        if not event.has_choice:
            return
        if isinstance(event.reasoning_content, str) and event.reasoning_content:
            self.data["reasoning_content"] += event.reasoning_content

        if isinstance(event.content, str) and event.content:
            self.data["content"] += event.content

        if event.tool_calls:
            self._process_tool_calls(event.tool_calls)

        if event.finish_reason:
            self.data["finish_reason"] = event.finish_reason

    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> None:
        """按显式 index、ID 或最近上下文聚合工具调用分块。"""
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            function = tc.get("function")
            if not isinstance(function, dict):
                continue
            index = self.tool_call_index_state.resolve(tc)
            if index is None:
                continue
            tool_id = tc.get("id")
            current = self.tool_call_map.get(index)
            if current is None:
                current = {
                    "id": tool_id,
                    "type": tc.get("type", "function"),
                    "function": {"name": "", "arguments": ""},
                }
                self.tool_call_map[index] = current
            elif tool_id is not None:
                current["id"] = tool_id

            if tc.get("type"):
                current["type"] = tc["type"]
            if isinstance(function.get("name"), str) and function["name"]:
                current["function"]["name"] = function["name"]
            if isinstance(function.get("arguments"), str) and function["arguments"]:
                current["function"]["arguments"] += function["arguments"]

    def finalize(self) -> Dict[str, Any]:
        """完成聚合并返回最终非流式响应。"""
        indexes = sorted(self.tool_call_map)
        tool_calls = [self.tool_call_map[index] for index in indexes]

        final_message = {"role": "assistant", "content": self.data["content"]}
        if self.data["reasoning_content"]:
            final_message["reasoning_content"] = self.data["reasoning_content"]
        if tool_calls:
            final_message["tool_calls"] = tool_calls

        finish_reason = self.data["finish_reason"] or ("tool_calls" if tool_calls else "stop")

        final_response = {
            "id": self.response_context.response_id,
            "object": "chat.completion",
            "created": self.response_context.created,
            "model": self.response_context.model,
            "choices": [
                {
                    "index": 0,
                    "message": final_message,
                    "finish_reason": finish_reason,
                    "logprobs": None,
                }
            ],
        }

        if self.data["usage"]:
            final_response["usage"] = self.data["usage"]
        if self.data["system_fingerprint"]:
            final_response["system_fingerprint"] = self.data["system_fingerprint"]

        return final_response


class CodeBuddyStreamService:
    """CodeBuddy 流式服务。"""

    def __init__(
            self,
            http_client_factory: Callable[[], Any] = get_http_client,
            api_url_factory: Callable[[], str] = get_codebuddy_api_url,
            first_chunk_timeout: float = 310.0,
            observer: Optional[StreamObserver] = None,
    ):
        self.connection_manager = SSEConnectionManager()
        self.http_client_factory = http_client_factory
        self.api_url_factory = api_url_factory
        self.first_chunk_timeout = first_chunk_timeout
        self.observer = observer

    def _observe(self, observation: StreamObservation) -> None:
        if self.observer is None:
            return
        try:
            self.observer(observation)
        except Exception:
            logger.exception("CodeBuddy 请求观察器执行失败")

    def _observe_retry(self, retry_count: int) -> None:
        self._observe(StreamObservation(
            kind="retry",
            retry_count=retry_count,
            error_type="upstream_connect_error",
        ))

    def _observe_error(self, error_type: str, status_code: Optional[int] = None) -> None:
        self._observe(StreamObservation(
            kind="error",
            error_type=error_type,
            status_code=status_code,
        ))

    def _observe_upstream_event(
            self,
            event: CodeBuddyResponseEvent,
            new_tool_call_count: int,
    ) -> None:
        usage = copy.deepcopy(event.usage) if isinstance(event.usage, dict) else None
        finish_reason = event.finish_reason if isinstance(event.finish_reason, str) else None
        self._observe(StreamObservation(
            kind="upstream_event",
            has_reasoning_content=(
                isinstance(event.reasoning_content, str) and bool(event.reasoning_content)
            ),
            has_content=isinstance(event.content, str) and bool(event.content),
            tool_call_count=new_tool_call_count,
            usage=usage,
            finish_reason=finish_reason,
            upstream_model=(
                event.chunk_data.get("model")
                if isinstance(event.chunk_data.get("model"), str)
                else None
            ),
        ))

    def _observe_client_disconnect(self) -> None:
        self._observe(StreamObservation(kind="client_disconnect"))

    @staticmethod
    def _create_response_context(
            payload: Dict[str, Any],
            response_model: Optional[str],
    ) -> CompletionResponseContext:
        return CompletionResponseContext(
            response_id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            model=str(response_model or payload.get("model") or "unknown"),
        )

    def _handle_api_error(
            self,
            status_code: int,
            error_msg: str,
            *,
            error_type: Optional[str] = None,
            code: Any = None,
            headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """统一的 API 错误处理。"""
        logger.error(f"CodeBuddy API错误: {status_code} - {error_msg}")

        if status_code == 401:
            mapped_status, default_type = 401, "authentication_error"
        elif status_code == 429:
            mapped_status, default_type = 429, "rate_limit_error"
        elif status_code >= 500:
            mapped_status, default_type = 502, "upstream_server_error"
        elif status_code < 400:
            mapped_status, default_type = 502, "upstream_protocol_error"
            error_msg = f"CodeBuddy API unexpected status: {status_code}"
            error_type = None
            code = None
        else:
            mapped_status, default_type = status_code, "upstream_error"

        raise UpstreamAPIError(
            status_code=mapped_status,
            message=error_msg,
            error_type=error_type or default_type,
            code=code,
            headers=headers,
        )

    @staticmethod
    def _parse_upstream_error_body(error_msg: str) -> tuple[str, Optional[str], Any]:
        try:
            error_value = json.loads(error_msg)
        except json.JSONDecodeError:
            return error_msg, None, None
        message, error_type, code = _extract_error_fields(
            error_value,
            error_msg,
            "",
        )
        return message, error_type, code

    @staticmethod
    def _upstream_sse_error(event: Dict[str, Any]) -> UpstreamAPIError:
        message, error_type, code = _extract_error_fields(
            event.get("error"),
            "CodeBuddy upstream stream error",
            "upstream_error",
        )
        return UpstreamAPIError(
            status_code=502,
            message=message,
            error_type=error_type,
            code=code,
        )

    @staticmethod
    def _incomplete_stream_error() -> UpstreamAPIError:
        return UpstreamAPIError(
            status_code=502,
            message="CodeBuddy upstream stream ended without a completion marker",
            error_type="upstream_incomplete",
        )

    async def _raise_upstream_api_error(self, response: Any) -> None:
        """尽力读取错误体，但始终以已经收到的上游状态码为准。"""
        try:
            error_text = await response.aread()
            error_msg = error_text.decode("utf-8", errors="ignore")
        except httpx.HTTPError as error:
            logger.warning("读取 CodeBuddy API 错误响应体失败: %s", error)
            error_msg = "unable to read upstream error response body"
        message, error_type, code = self._parse_upstream_error_body(error_msg)
        retry_after = getattr(response, "headers", {}).get("Retry-After")
        forwarded_headers = {"Retry-After": retry_after} if retry_after else None
        self._handle_api_error(
            response.status_code,
            message,
            error_type=error_type,
            code=code,
            headers=forwarded_headers,
        )

    async def _iter_normalized_upstream_events(
            self,
            response: Any,
    ) -> AsyncIterator[Any]:
        """统一解析上游 SSE，并把对象事件转换为共享响应语义。"""
        observed_tool_call_indexes: set[int] = set()
        observation_index_state = ToolCallIndexState()
        try:
            async for event in iter_sse_events(response.aiter_text()):
                if event is SSE_DONE:
                    self._observe(StreamObservation(
                        kind="upstream_event",
                        upstream_done=True,
                    ))
                    yield SSE_DONE
                    return
                if not isinstance(event, dict):
                    self._observe(StreamObservation(kind="upstream_event"))
                    yield event
                    continue
                if "error" in event:
                    self._observe(StreamObservation(kind="upstream_event"))
                    raise self._upstream_sse_error(event)
                response_event = CodeBuddyResponseEvent.parse(event)
                new_tool_call_count = 0
                for tool_call in response_event.tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call_index = observation_index_state.resolve(tool_call)
                    if (
                            tool_call_index is not None
                            and tool_call_index not in observed_tool_call_indexes
                    ):
                        observed_tool_call_indexes.add(tool_call_index)
                        new_tool_call_count += 1
                self._observe_upstream_event(response_event, new_tool_call_count)
                yield response_event
        except SSEDataError as error:
            raise UpstreamAPIError(
                status_code=502,
                message=str(error),
                error_type="upstream_protocol_error",
            ) from error

    async def handle_stream_response(
            self,
            payload: Dict[str, Any],
            headers: Dict[str, str],
            *,
            response_model: Optional[str] = None,
    ) -> StreamingResponse:
        """处理流式响应。"""
        response_context = self._create_response_context(payload, response_model)

        async def stream_core():
            client = await self.http_client_factory()
            async with client.stream("POST", self.api_url_factory(), json=payload, headers=headers) as response:
                if response.status_code != 200:
                    await self._raise_upstream_api_error(response)

                tool_call_index_state = ToolCallIndexState()
                stream_normalizer = OpenAIStreamNormalizer()
                saw_finish_reason = False

                async for event in self._iter_normalized_upstream_events(response):
                    if event is SSE_DONE:
                        yield format_sse_done()
                        return
                    if not isinstance(event, CodeBuddyResponseEvent):
                        yield format_sse_event(event)
                        continue

                    if event.finish_reason is not None:
                        saw_finish_reason = True
                    converted_chunk = add_openai_tool_call_indexes(
                        event,
                        tool_call_index_state,
                    )
                    converted_chunk = normalize_openai_stream_chunk_envelope(
                        converted_chunk,
                        response_context,
                    )

                    for outgoing_chunk in stream_normalizer.normalize(converted_chunk):
                        yield format_sse_event(outgoing_chunk)

                if saw_finish_reason:
                    yield format_sse_done()
                    return
                raise self._incomplete_stream_error()

        managed_stream = self.connection_manager.stream_with_retry(
            stream_core,
            on_retry=self._observe_retry,
        )

        async def prefetch_first_chunk():
            try:
                return await asyncio.wait_for(
                    anext(managed_stream),
                    timeout=self.first_chunk_timeout,
                )
            except TimeoutError:
                logger.error("等待 CodeBuddy 首个响应事件超时")
                self._observe_error("upstream_timeout", 504)
                raise UpstreamAPIError(
                    status_code=504,
                    message="CodeBuddy API first chunk timeout",
                    error_type="upstream_timeout",
                )
            except httpx.TimeoutException:
                logger.error("CodeBuddy API 超时")
                self._observe_error("upstream_timeout", 504)
                raise UpstreamAPIError(
                    status_code=504,
                    message="CodeBuddy API timeout",
                    error_type="upstream_timeout",
                )
            except httpx.TransportError as error:
                logger.error("上游传输错误: %s", error)
                self._observe_error("upstream_transport_error", 502)
                raise UpstreamAPIError(
                    status_code=502,
                    message=f"Upstream transport error: {str(error)}",
                    error_type="upstream_transport_error",
                ) from error
            except UpstreamAPIError as error:
                self._observe_error(error.error["type"], error.status_code)
                raise
            except Exception:
                self._observe_error("stream_error")
                raise

        async def response_body():
            try:
                first_chunk = await prefetch_first_chunk()
                yield first_chunk
                try:
                    async for chunk in managed_stream:
                        yield chunk
                except httpx.TimeoutException as error:
                    self._observe_error("upstream_timeout", 504)
                    yield format_sse_error(str(error), "upstream_timeout")
                except httpx.TransportError as error:
                    self._observe_error("upstream_transport_error", 502)
                    yield format_sse_error(str(error), "upstream_transport_error")
                except UpstreamAPIError as error:
                    self._observe_error(error.error["type"], error.status_code)
                    yield format_sse_event({"error": error.error})
                except Exception as error:
                    self._observe_error("stream_error")
                    yield format_sse_error(str(error), "stream_error")
            finally:
                await managed_stream.aclose()

        return _ManagedStreamingResponse(
            response_body(),
            managed_stream.aclose,
            self._observe_client_disconnect,
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    async def handle_non_stream_response(
            self,
            payload: Dict[str, Any],
            headers: Dict[str, str],
            *,
            response_model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """聚合上游流式响应，返回 OpenAI 非流式响应。"""
        response_context = self._create_response_context(payload, response_model)

        async def request_once() -> Dict[str, Any]:
            client = await self.http_client_factory()
            async with client.stream(
                    "POST",
                    self.api_url_factory(),
                    json=payload,
                    headers=headers,
            ) as response:
                if response.status_code != 200:
                    await self._raise_upstream_api_error(response)

                aggregator = StreamResponseAggregator(response_context)
                saw_completion_marker = False
                async for event in self._iter_normalized_upstream_events(response):
                    if event is SSE_DONE:
                        saw_completion_marker = True
                        break
                    if not isinstance(event, CodeBuddyResponseEvent):
                        continue
                    if event.finish_reason is not None:
                        saw_completion_marker = True
                    aggregator.process_event(event)

                if not saw_completion_marker:
                    raise self._incomplete_stream_error()

            return aggregator.finalize()

        try:
            return await self.connection_manager.run_with_retry(
                request_once,
                on_retry=self._observe_retry,
            )
        except UpstreamAPIError as error:
            self._observe_error(error.error["type"], error.status_code)
            raise
        except httpx.TimeoutException:
            logger.error("CodeBuddy API 超时")
            self._observe_error("upstream_timeout", 504)
            raise UpstreamAPIError(
                status_code=504,
                message="CodeBuddy API timeout",
                error_type="upstream_timeout",
            )
        except httpx.TransportError as e:
            logger.error("上游传输错误: %s", e)
            self._observe_error("upstream_transport_error", 502)
            raise UpstreamAPIError(
                status_code=502,
                message=f"Upstream transport error: {str(e)}",
                error_type="upstream_transport_error",
            )
        except Exception:
            self._observe_error("stream_error")
            raise
