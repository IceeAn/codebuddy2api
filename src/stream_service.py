"""CodeBuddy 上游流式调用服务。"""
import asyncio
import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .openai_compat import (
    OpenAICompatibilityConverter,
    OpenAIStreamNormalizer,
    ensure_openai_stream_chunk_fields,
    validate_and_fix_tool_call_args,
)
from .sse import (
    SSE_DONE,
    SSE_HEADERS,
    format_sse_done,
    format_sse_error,
    format_sse_event,
    iter_sse_events,
)

logger = logging.getLogger(__name__)


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
    """SSE 连接管理器，包含重连逻辑。"""

    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def stream_with_retry(self, stream_func, *args, **kwargs):
        """带重连的流式处理。"""
        for attempt in range(self.max_retries + 1):
            try:
                async for chunk in stream_func(*args, **kwargs):
                    yield chunk
                break
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt < self.max_retries:
                    wait_time = self.retry_delay * (2 ** attempt)
                    logger.warning(f"连接失败，{wait_time}秒后重试 (第{attempt + 1}次): {e}")
                    yield format_sse_error(
                        f"Connection lost, retrying in {wait_time}s... (attempt {attempt + 1})",
                        "connection_retry",
                    )
                    await asyncio.sleep(wait_time)
                    continue

                logger.error(f"重连失败，已达到最大重试次数: {e}")
                yield format_sse_error(
                    f"Connection failed after {self.max_retries} retries: {str(e)}",
                    "connection_failed",
                )
                raise
            except Exception as e:
                logger.error(f"流式处理异常: {e}")
                yield format_sse_error(f"Stream error: {str(e)}", "stream_error")
                raise


class StreamResponseAggregator:
    """流式响应聚合器，使用工具调用 ID 保持多工具调用顺序。"""

    def __init__(self):
        self.data = {
            "id": None,
            "model": None,
            "content": "",
            "reasoning_content": "",
            "tool_calls": [],
            "finish_reason": None,
            "usage": None,
            "system_fingerprint": None,
        }
        self.tool_call_map = {}
        self.tool_call_order = []
        self.current_tool_id = None

    def process_chunk(self, obj: Dict[str, Any]):
        """处理单个响应 chunk。"""
        self.data["id"] = self.data["id"] or obj.get("id")
        self.data["model"] = self.data["model"] or obj.get("model")
        self.data["system_fingerprint"] = obj.get("system_fingerprint") or self.data["system_fingerprint"]

        if obj.get("usage"):
            self.data["usage"] = obj.get("usage")

        choices = obj.get("choices", [])
        if not choices:
            return

        choice = choices[0]
        delta = choice.get("delta", {})

        if delta.get("reasoning_content"):
            self.data["reasoning_content"] += delta.get("reasoning_content")

        if delta.get("content"):
            self.data["content"] += delta.get("content")

        if delta.get("tool_calls"):
            self._process_tool_calls(delta.get("tool_calls"))

        if choice.get("finish_reason"):
            self.data["finish_reason"] = choice.get("finish_reason")

    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]]):
        """处理工具调用分块。"""
        for tc in tool_calls:
            tool_id = tc.get("id")

            if tool_id:
                if tool_id not in self.tool_call_map:
                    self.tool_call_map[tool_id] = {
                        "id": tool_id,
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": "",
                            "arguments": "",
                        },
                    }
                    self.tool_call_order.append(tool_id)
                    self.current_tool_id = tool_id
                    logger.info(f"新工具调用: {tool_id}")
                else:
                    self.current_tool_id = tool_id

                if tc.get("type"):
                    self.tool_call_map[tool_id]["type"] = tc.get("type")

                func = tc.get("function", {})
                if func.get("name"):
                    self.tool_call_map[tool_id]["function"]["name"] = func.get("name")
                if func.get("arguments"):
                    self.tool_call_map[tool_id]["function"]["arguments"] += func.get("arguments")

            elif self.current_tool_id and self.current_tool_id in self.tool_call_map:
                func = tc.get("function", {})
                if func.get("name"):
                    self.tool_call_map[self.current_tool_id]["function"]["name"] = func.get("name")
                if func.get("arguments"):
                    self.tool_call_map[self.current_tool_id]["function"]["arguments"] += func.get("arguments")
            else:
                logger.warning("工具调用缺少 ID 且无当前工具调用上下文，跳过处理")

    def finalize(self) -> Dict[str, Any]:
        """完成聚合并返回最终非流式响应。"""
        if self.tool_call_map:
            self.data["tool_calls"] = []
            for tool_id in self.tool_call_order:
                if tool_id in self.tool_call_map:
                    tc = self.tool_call_map[tool_id]
                    tc["function"]["arguments"] = validate_and_fix_tool_call_args(
                        tc["function"]["arguments"]
                    )
                    self.data["tool_calls"].append(tc)
                    logger.info(f"工具调用: {tool_id} - {tc['function']['name']}")

            logger.info(f"成功聚合 {len(self.data['tool_calls'])} 个工具调用")

        final_message = {"role": "assistant", "content": self.data["content"]}
        if self.data["reasoning_content"]:
            final_message["reasoning_content"] = self.data["reasoning_content"]
        if self.data["tool_calls"]:
            final_message["tool_calls"] = self.data["tool_calls"]

        finish_reason = "tool_calls" if self.data["tool_calls"] else (self.data["finish_reason"] or "stop")

        final_response = {
            "id": self.data["id"] or str(uuid.uuid4()),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.data["model"] or "unknown",
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
    ):
        self.connection_manager = SSEConnectionManager(max_retries=3, retry_delay=1.0)
        self.http_client_factory = http_client_factory
        self.api_url_factory = api_url_factory

    def _handle_api_error(self, status_code: int, error_msg: str) -> None:
        """统一的 API 错误处理。"""
        logger.error(f"CodeBuddy API错误: {status_code} - {error_msg}")

        if status_code == 401:
            raise HTTPException(status_code=401, detail="CodeBuddy API authentication failed")
        if status_code == 429:
            raise HTTPException(status_code=429, detail="CodeBuddy API rate limit exceeded")
        if status_code >= 500:
            raise HTTPException(status_code=502, detail="CodeBuddy API server error")
        raise HTTPException(status_code=status_code, detail=f"CodeBuddy API error: {error_msg}")

    async def handle_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str]) -> StreamingResponse:
        """处理流式响应。"""

        async def stream_core():
            client = await self.http_client_factory()
            async with client.stream("POST", self.api_url_factory(), json=payload, headers=headers) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    error_msg = error_text.decode("utf-8", errors="ignore")
                    yield format_sse_error(f"CodeBuddy API error: {response.status_code} - {error_msg}", "api_error")
                    return

                tool_call_index_map = {}
                fallback_stream_id = f"chatcmpl-{uuid.uuid4().hex}"
                fallback_created = int(time.time())
                fallback_model = str(payload.get("model") or "unknown")
                stream_normalizer = OpenAIStreamNormalizer()

                async for event in iter_sse_events(response.aiter_text(chunk_size=8192)):
                    if event is SSE_DONE:
                        yield format_sse_done()
                        return
                    if not isinstance(event, dict):
                        continue

                    converted_chunk = OpenAICompatibilityConverter.convert_sse_chunk_to_openai_format(
                        event,
                        tool_call_index_map,
                    )
                    converted_chunk = ensure_openai_stream_chunk_fields(
                        converted_chunk,
                        fallback_stream_id,
                        fallback_created,
                        fallback_model,
                    )

                    for outgoing_chunk in stream_normalizer.normalize(converted_chunk):
                        yield format_sse_event(outgoing_chunk)

        async def stream_with_retry():
            async for chunk in self.connection_manager.stream_with_retry(stream_core):
                yield chunk

        return StreamingResponse(stream_with_retry(), media_type="text/event-stream", headers=SSE_HEADERS)

    async def handle_non_stream_response(self, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        """聚合上游流式响应，返回 OpenAI 非流式响应。"""
        try:
            client = await self.http_client_factory()
            response = await client.post(self.api_url_factory(), json=payload, headers=headers)

            if response.status_code != 200:
                error_msg = response.text
                self._handle_api_error(response.status_code, error_msg)

            aggregator = StreamResponseAggregator()
            async for event in iter_sse_events(response.aiter_text()):
                if event is SSE_DONE:
                    break
                if isinstance(event, dict):
                    aggregator.process_chunk(event)

            return aggregator.finalize()

        except httpx.TimeoutException:
            logger.error("CodeBuddy API 超时")
            raise HTTPException(status_code=504, detail="CodeBuddy API timeout")
        except httpx.NetworkError as e:
            logger.error(f"网络错误: {e}")
            raise HTTPException(status_code=502, detail=f"Network error: {str(e)}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"请求异常: {e}")
            raise HTTPException(status_code=500, detail=f"Request error: {str(e)}")
