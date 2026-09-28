"""CodeBuddy 上游流式调用服务。"""
import asyncio
import copy
import json
import logging
import random
import re
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Literal, Optional, Protocol

import httpx
import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect

from .codebuddy_events import (
    CodeBuddyResponseEvent,
    SSE_DONE,
    ToolCallIndexState,
    UpstreamProtocolViolation,
)
from .openai_response import StreamResponseAggregator
from .openai_errors import openai_error_content
from .security_logging import safe_error_text
from .openai_compat import (
    CompletionResponseContext,
    OpenAIStreamNormalizer,
    add_openai_tool_call_indexes,
    normalize_openai_stream_chunk_envelope,
)
from .sse import (
    SSEDataError,
    SSE_HEADERS,
    format_sse_done,
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
    """由各下游协议使用中立字段稳定序列化的上游错误。"""

    def __init__(
            self,
            status_code: int,
            message: str,
            error_type: str,
            *,
            code: Any = None,
            headers: Optional[Dict[str, str]] = None,
            upstream_status_code: Optional[int] = None,
    ):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.message = message
        self.error_type = error_type
        self.code = code
        self.safe_headers = headers or {}
        self.upstream_status_code = upstream_status_code
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
        message = candidate.get("message") or candidate.get("msg")
        error_type = candidate.get("type")
        code = candidate.get("code")
        return (
            safe_error_text(message) if isinstance(message, str) and message else fallback_message,
            error_type if isinstance(error_type, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_type) else fallback_type,
            safe_error_text(code)[:128] if isinstance(code, str) else code if type(code) is int else None,
        )
    if isinstance(candidate, str) and candidate:
        return safe_error_text(candidate), fallback_type, None
    return fallback_message, fallback_type, None


def get_codebuddy_api_url() -> str:
    """动态加载 CodeBuddy API URL，确保安全白名单即时生效。"""
    from config import get_codebuddy_api_endpoint

    return f"{get_codebuddy_api_endpoint()}/v2/chat/completions"


def get_http_ssl_verify() -> bool:
    """读取 SSL 验证设置，禁用时明确告警。"""
    from config import get_ssl_verify

    ssl_verify = get_ssl_verify()
    if not ssl_verify:
        logger.warning("SSL验证已禁用，仅应在受控调试环境使用。")
    return ssl_verify


HTTP_CLIENT_CONFIG = {
    "verify": get_http_ssl_verify(),
    "timeout": httpx.Timeout(300.0, connect=30.0, read=300.0),
    "limits": httpx.Limits(max_keepalive_connections=20, max_connections=100),
    "trust_env": False,
}

_http_client_pool: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """获取全局 HTTP 客户端池。"""
    global _http_client_pool
    client = _http_client_pool
    if client is None:
        async with _client_lock:
            if _http_client_pool is None:
                _http_client_pool = httpx.AsyncClient(**HTTP_CLIENT_CONFIG)
            client = _http_client_pool
    return client


async def close_http_client():
    """关闭全局 HTTP 客户端池。"""
    global _http_client_pool
    async with _client_lock:
        client = _http_client_pool
        if client is not None:
            await client.aclose()
        _http_client_pool = None


async def startup_http_client() -> None:
    """应用启动时初始化连接池。"""
    logger.info("CodeBuddy Router 启动中...")
    await get_http_client()
    logger.info("HTTP 连接池已初始化")


async def shutdown_http_client() -> None:
    """应用关闭时清理连接池。"""
    logger.info("CodeBuddy Router 关闭中...")
    await close_http_client()
    logger.info("资源清理完成")


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

    def __init__(self, content, close_callback, disconnect_callback, *, timeout, **kwargs):
        super().__init__(content, **kwargs)
        self._close_callback = close_callback
        self._disconnect_callback = disconnect_callback
        self._timeout = timeout

    async def _stream_from_first_chunk(self, first_chunk: Any, send) -> None:
        self.body_iterator = _prepend_chunk(first_chunk, self.body_iterator)
        await super().stream_response(send)

    async def __call__(self, _scope, receive, send) -> None:
        deadline = asyncio.get_running_loop().time() + self._timeout

        async def bounded_send(message):
            try:
                # 直接在当前任务发送，避免 Python 3.10 wait_for 的完成/取消竞争。
                with anyio.fail_after(max(0, deadline - asyncio.get_running_loop().time())):
                    await send(message)
            except TimeoutError as error:  # AnyIO 明确抛出内置 TimeoutError。
                raise ClientDisconnect() from error

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
                    await self._stream_from_first_chunk(first_chunk, bounded_send)
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




@dataclass
class _OpenAIStreamState:
    normalizer: OpenAIStreamNormalizer
    tool_indexes: ToolCallIndexState
    saw_finish_reason: bool = False
    usage: Optional[Dict[str, Any]] = None
    usage_metadata: Dict[str, Any] = field(default_factory=dict)


class OpenAIDownstreamAdapter:
    """保持既有 OpenAI 下游字节契约的响应适配器。"""

    media_type = "text/event-stream"
    stream_headers = SSE_HEADERS

    def __init__(self, context: CompletionResponseContext, *, include_usage: bool = False):
        self.context = context
        self.include_usage = include_usage

    def create_stream_state(self) -> _OpenAIStreamState:
        return _OpenAIStreamState(OpenAIStreamNormalizer(), ToolCallIndexState())

    def process_stream_event(self, state: _OpenAIStreamState, event: Any) -> List[str]:
        if not isinstance(event, CodeBuddyResponseEvent):
            return [format_sse_event(event)]
        if event.finish_reason is not None:
            state.saw_finish_reason = True
        if isinstance(event.usage, dict):
            state.usage = copy.deepcopy(event.usage)
        if not event.has_choice and "usage" in event.chunk_data:
            state.usage_metadata.update(copy.deepcopy({
                key: value for key, value in event.chunk_data.items()
                if key not in ("choices", "usage")
            }))
            return []
        converted_chunk = add_openai_tool_call_indexes(event, state.tool_indexes)
        converted_chunk = normalize_openai_stream_chunk_envelope(converted_chunk, self.context)
        converted_chunk.pop("usage", None)
        if self.include_usage:
            converted_chunk["usage"] = None
        return [
            format_sse_event(chunk)
            for chunk in state.normalizer.normalize(converted_chunk)
        ]

    def finalize_stream(self, state: _OpenAIStreamState, upstream_done: bool) -> List[str]:
        if upstream_done or state.saw_finish_reason:
            output = []
            if self.include_usage and state.usage is not None:
                output.append(format_sse_event(normalize_openai_stream_chunk_envelope(
                    {**state.usage_metadata, "choices": [], "usage": state.usage}, self.context,
                )))
            return output + [format_sse_done()]
        raise CodeBuddyStreamService._incomplete_stream_error()

    @staticmethod
    def format_stream_error(error: Any) -> str:
        if isinstance(error, UpstreamAPIError):
            return format_sse_event(openai_error_content(
                error.status_code, error.message, error_type=error.error_type, code=error.code,
            ))
        return format_sse_event(openai_error_content(500, "Upstream stream error", error_type="stream_error"))

    def create_non_stream_aggregator(self) -> StreamResponseAggregator:
        return StreamResponseAggregator(self.context)

    @staticmethod
    def process_non_stream_event(aggregator: StreamResponseAggregator, event: Any) -> None:
        if isinstance(event, CodeBuddyResponseEvent):
            aggregator.process_event(event)

    @staticmethod
    def finalize_non_stream(
            aggregator: StreamResponseAggregator,
            upstream_done: bool,
    ) -> Dict[str, Any]:
        if not upstream_done and aggregator.data["finish_reason"] is None:
            raise CodeBuddyStreamService._incomplete_stream_error()
        return aggregator.finalize()


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
        logger.error("CodeBuddy API错误: %s", status_code)

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
            upstream_status_code=status_code,
        )

    @staticmethod
    def _parse_upstream_error_body(error_msg: str) -> tuple[str, Optional[str], Any]:
        try:
            error_value = json.loads(error_msg)
        except json.JSONDecodeError:
            return "CodeBuddy upstream error", None, None
        message, error_type, code = _extract_error_fields(
            error_value,
            "CodeBuddy upstream error",
            "",
        )
        return message, error_type, code

    @staticmethod
    def _upstream_sse_error(event: Dict[str, Any]) -> UpstreamAPIError:
        error_value = event.get("error")
        fallback_message = "CodeBuddy upstream stream error"
        message, error_type, code = _extract_error_fields(
            error_value,
            fallback_message,
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

    async def _raise_upstream_api_error(self, response: Any, sensitive_values=()) -> None:
        """尽力读取错误体，但始终以已经收到的上游状态码为准。"""
        try:
            from config import get_security_limit
            limit = get_security_limit("CODEBUDDY_MAX_UPSTREAM_ERROR_BYTES")
            error_text = bytearray()
            async for chunk in response.aiter_bytes():
                if len(error_text) + len(chunk) > limit:
                    error_text = bytearray(b"upstream error body exceeds byte limit")
                    break
                error_text.extend(chunk)
            error_msg = error_text.decode("utf-8", errors="replace")
            for value in sensitive_values:
                if value:
                    error_msg = error_msg.replace(value, "[已隐藏]")
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
            sensitive_values=(),
    ) -> AsyncIterator[Any]:
        """统一解析上游 SSE，并把对象事件转换为共享响应语义。"""
        observed_tool_call_indexes: set[int] = set()
        observation_index_state = ToolCallIndexState()
        from config import get_security_limit
        try:
            async for event in iter_sse_events(response.aiter_text(), max_line_bytes=get_security_limit("CODEBUDDY_MAX_SSE_LINE_BYTES"), max_total_bytes=get_security_limit("CODEBUDDY_MAX_UPSTREAM_RESPONSE_BYTES")):
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
                    serialized = json.dumps(event)
                    for value in sensitive_values:
                        if value:
                            serialized = serialized.replace(json.dumps(value)[1:-1], "[已隐藏]")
                    raise self._upstream_sse_error(json.loads(serialized))
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

    async def _deadline_stream(self, iterator, timeout: float):
        """重试、心跳和工具缓冲共享总期限，取消时关闭在途迭代器。"""
        deadline = asyncio.get_running_loop().time() + timeout
        try:
            while True:
                try:
                    with anyio.fail_after(max(0, deadline - asyncio.get_running_loop().time())):
                        chunk = await anext(iterator)
                except StopAsyncIteration:
                    break
                except TimeoutError as error:  # AnyIO 在 Python 3.10 同样使用内置异常。
                    raise UpstreamAPIError(504, "CodeBuddy API total timeout", "upstream_timeout") from error
                yield chunk
        finally:
            await iterator.aclose()

    @staticmethod
    def _sensitive_values(headers):
        authorization = headers.get("Authorization", "")
        return (authorization, authorization.removeprefix("Bearer "))

    async def handle_stream_response(
            self,
            payload: Dict[str, Any],
            headers: Dict[str, str],
            *,
            response_model: Optional[str] = None,
            response_adapter: Optional[Any] = None,
            include_usage: bool = False,
    ) -> StreamingResponse:
        """处理流式响应。"""
        response_context = self._create_response_context(payload, response_model)
        adapter = response_adapter or OpenAIDownstreamAdapter(response_context, include_usage=include_usage)
        sensitive_values = self._sensitive_values(headers)

        async def stream_core():
            client = await self.http_client_factory()
            async with client.stream("POST", self.api_url_factory(), json=payload, headers=headers) as response:
                if response.status_code != 200:
                    await self._raise_upstream_api_error(response, sensitive_values)

                state = adapter.create_stream_state()
                try:
                    async for event in self._iter_normalized_upstream_events(response, sensitive_values):
                        if event is SSE_DONE:
                            for outgoing_chunk in adapter.finalize_stream(state, True):
                                yield outgoing_chunk
                            return
                        for outgoing_chunk in adapter.process_stream_event(state, event):
                            yield outgoing_chunk

                    for outgoing_chunk in adapter.finalize_stream(state, False):
                        yield outgoing_chunk
                except Exception as error:
                    if isinstance(error, UpstreamProtocolViolation):
                        raise UpstreamAPIError(
                            status_code=502,
                            message=str(error),
                            error_type="upstream_protocol_error",
                        ) from error
                    raise

        from config import get_security_limit
        managed_stream = self._deadline_stream(
            self.connection_manager.stream_with_retry(stream_core, on_retry=self._observe_retry),
            get_security_limit("CODEBUDDY_UPSTREAM_TIMEOUT_SECONDS"),
        )

        async def prefetch_first_chunk():
            try:
                return await asyncio.wait_for(
                    anext(managed_stream),
                    timeout=self.first_chunk_timeout,
                )
            except asyncio.TimeoutError:
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
                    message="Upstream transport error",
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
                    yield adapter.format_stream_error(UpstreamAPIError(
                        status_code=504,
                        message="CodeBuddy API timeout",
                        error_type="upstream_timeout",
                    ))
                except httpx.TransportError as error:
                    self._observe_error("upstream_transport_error", 502)
                    yield adapter.format_stream_error(UpstreamAPIError(
                        status_code=502,
                        message="Upstream transport error",
                        error_type="upstream_transport_error",
                    ))
                except UpstreamAPIError as error:
                    self._observe_error(error.error["type"], error.status_code)
                    yield adapter.format_stream_error(error)
                except Exception as error:
                    self._observe_error("stream_error")
                    yield adapter.format_stream_error(error)
            finally:
                await managed_stream.aclose()

        return _ManagedStreamingResponse(
            response_body(),
            managed_stream.aclose,
            self._observe_client_disconnect,
            timeout=get_security_limit("CODEBUDDY_UPSTREAM_TIMEOUT_SECONDS"),
            media_type=adapter.media_type,
            headers=adapter.stream_headers,
        )

    async def handle_non_stream_response(
            self,
            payload: Dict[str, Any],
            headers: Dict[str, str],
            *,
            response_model: Optional[str] = None,
            response_adapter: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """聚合上游流式响应，返回 OpenAI 非流式响应。"""
        response_context = self._create_response_context(payload, response_model)
        adapter = response_adapter or OpenAIDownstreamAdapter(response_context)
        sensitive_values = self._sensitive_values(headers)

        async def request_once() -> Dict[str, Any]:
            client = await self.http_client_factory()
            async with client.stream(
                    "POST",
                    self.api_url_factory(),
                    json=payload,
                    headers=headers,
            ) as response:
                if response.status_code != 200:
                    await self._raise_upstream_api_error(response, sensitive_values)

                aggregator = adapter.create_non_stream_aggregator()
                upstream_done = False
                try:
                    async for event in self._iter_normalized_upstream_events(response, sensitive_values):
                        if event is SSE_DONE:
                            upstream_done = True
                            break
                        adapter.process_non_stream_event(aggregator, event)
                    return adapter.finalize_non_stream(aggregator, upstream_done)
                except Exception as error:
                    if isinstance(error, UpstreamProtocolViolation):
                        raise UpstreamAPIError(
                            status_code=502,
                            message=str(error),
                            error_type="upstream_protocol_error",
                        ) from error
                    raise

        timeout_scope = None
        try:
            from config import get_security_limit
            with anyio.fail_after(
                    get_security_limit("CODEBUDDY_UPSTREAM_TIMEOUT_SECONDS"),
            ) as timeout_scope:
                return await self.connection_manager.run_with_retry(
                    request_once,
                    on_retry=self._observe_retry,
                )
        except UpstreamAPIError as error:
            self._observe_error(error.error["type"], error.status_code)
            raise
        except httpx.TimeoutException as error:
            logger.error("CodeBuddy API 超时")
            self._observe_error("upstream_timeout", 504)
            raise UpstreamAPIError(
                status_code=504,
                message="CodeBuddy API timeout",
                error_type="upstream_timeout",
            ) from error
        except TimeoutError as error:  # AnyIO 在 Python 3.10 同样抛出内置 TimeoutError。
            if timeout_scope is None or not timeout_scope.cancel_called:
                self._observe_error("stream_error")
                raise
            logger.error("CodeBuddy API 超时")
            self._observe_error("upstream_timeout", 504)
            raise UpstreamAPIError(
                status_code=504,
                message="CodeBuddy API timeout",
                error_type="upstream_timeout",
            ) from error
        except httpx.TransportError as e:
            logger.error("上游传输错误: %s", e)
            self._observe_error("upstream_transport_error", 502)
            raise UpstreamAPIError(
                status_code=502,
                message="Upstream transport error",
                error_type="upstream_transport_error",
            )
        except Exception:
            self._observe_error("stream_error")
            raise
