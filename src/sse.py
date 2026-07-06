"""SSE 事件解析和格式化工具。"""
import json
from typing import Any, AsyncIterator

SSE_DONE = object()


class SSEDataError(ValueError):
    """上游 SSE data 字段不是可解析的 JSON。"""


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}


def format_sse_error(message: str, error_type: str = "stream_error") -> str:
    """格式化 SSE 错误响应。"""
    error_data = {
        "error": {
            "message": message,
            "type": error_type,
        }
    }
    return format_sse_event(error_data)


def format_sse_event(data: Any) -> str:
    """格式化 data-only SSE 事件，OpenAI 兼容客户端依赖空行作为事件边界。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def format_sse_done() -> str:
    return "data: [DONE]\n\n"


def parse_sse_event(line: str) -> Any:
    """解析单行 SSE 事件，返回 JSON 对象、SSE_DONE 或 None。"""
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if stripped != "data:" and not stripped.startswith("data: "):
        return None

    data = stripped[5:].strip()
    if not data:
        return None
    if data == "[DONE]":
        return SSE_DONE

    try:
        return json.loads(data)
    except json.JSONDecodeError as error:
        raise SSEDataError("upstream SSE data contains invalid JSON") from error


async def iter_sse_events(chunks: AsyncIterator[str]) -> AsyncIterator[Any]:
    """从任意文本分块中统一解析 SSE 事件。"""
    buffer = ""
    async for chunk in chunks:
        if not chunk:
            continue

        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            event = parse_sse_event(line)
            if event is not None:
                yield event

    if buffer.strip():
        event = parse_sse_event(buffer.strip())
        if event is not None:
            yield event
