"""CodeBuddy 中立事件到 Anthropic Messages 响应的编码器。"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .anthropic_compat import anthropic_thinking_signature
from .codebuddy_events import CodeBuddyResponseEvent, ToolCallIndexState


class UpstreamProtocolViolation(ValueError):
    """上游事件无法严格编码为 Anthropic 协议。"""


@dataclass(frozen=True)
class AnthropicResponseContext:
    message_id: str
    request_id: str
    model: str


def format_anthropic_sse(event_name: str, data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {payload}\n\n"


def format_anthropic_stream_error(
        request_id: str,
        error_type: str,
        message: str,
) -> str:
    return format_anthropic_sse("error", {
        "type": "error",
        "error": {"type": error_type, "message": message},
        "request_id": request_id,
    })


def map_finish_reason(finish_reason: str) -> str:
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "content_filter": "refusal",
    }
    mapped = mapping.get(finish_reason)
    if mapped is None:
        raise UpstreamProtocolViolation(f"Unsupported CodeBuddy finish_reason: {finish_reason}")
    return mapped


def map_usage(usage: Any) -> Dict[str, int]:
    if not isinstance(usage, dict):
        raise UpstreamProtocolViolation("CodeBuddy usage must be an object")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if (
        not isinstance(prompt_tokens, int)
        or isinstance(prompt_tokens, bool)
        or prompt_tokens < 0
        or not isinstance(completion_tokens, int)
        or isinstance(completion_tokens, bool)
        or completion_tokens < 0
    ):
        raise UpstreamProtocolViolation(
            "CodeBuddy usage must contain non-negative integer prompt_tokens and completion_tokens"
        )
    return {"input_tokens": prompt_tokens, "output_tokens": completion_tokens}


@dataclass
class _ToolState:
    upstream_index: int
    tool_id: Optional[str] = None
    name: Optional[str] = None
    arguments: str = ""
    content_index: Optional[int] = None
    started: bool = False
    closed: bool = False

    def merge(self, tool_call: Dict[str, Any]) -> str:
        tool_type = tool_call.get("type")
        if tool_type is not None and tool_type != "function":
            raise UpstreamProtocolViolation("CodeBuddy tool call type must be function")
        tool_id = tool_call.get("id")
        if tool_id is not None:
            if not isinstance(tool_id, str) or not tool_id:
                raise UpstreamProtocolViolation("CodeBuddy tool call id must be a non-empty string")
            if self.tool_id is not None and self.tool_id != tool_id:
                raise UpstreamProtocolViolation("CodeBuddy tool call id changed between chunks")
            self.tool_id = tool_id

        function = tool_call.get("function")
        if function is None:
            return ""
        if not isinstance(function, dict):
            raise UpstreamProtocolViolation("CodeBuddy tool call function must be an object")
        name = function.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise UpstreamProtocolViolation("CodeBuddy tool name must be a string")
            if name:
                if self.name is not None and self.name != name:
                    raise UpstreamProtocolViolation("CodeBuddy tool name changed between chunks")
                self.name = name
        arguments = function.get("arguments")
        if arguments is None:
            return ""
        if not isinstance(arguments, str):
            raise UpstreamProtocolViolation("CodeBuddy tool arguments delta must be a string")
        self.arguments += arguments
        return arguments

    def parsed_input(self) -> Dict[str, Any]:
        if self.tool_id is None or self.name is None:
            raise UpstreamProtocolViolation("CodeBuddy tool call is missing id or name")
        try:
            parsed = json.loads(self.arguments)
        except json.JSONDecodeError as error:
            raise UpstreamProtocolViolation("CodeBuddy tool arguments are not valid JSON") from error
        if not isinstance(parsed, dict):
            raise UpstreamProtocolViolation("CodeBuddy tool arguments must decode to an object")
        return parsed


class _AnthropicEventConsumer:
    def __init__(self) -> None:
        self.finish_reason: Optional[str] = None
        self.usage: Optional[Dict[str, int]] = None
        self.tool_index_state = ToolCallIndexState()
        self.tools: Dict[int, _ToolState] = {}

    def _capture_finish_and_usage(self, event: CodeBuddyResponseEvent) -> None:
        if event.finish_reason is not None:
            mapped = map_finish_reason(event.finish_reason)
            if self.finish_reason is not None and self.finish_reason != mapped:
                raise UpstreamProtocolViolation("CodeBuddy finish_reason changed between chunks")
            self.finish_reason = mapped
        if "usage" in event.chunk_data and event.chunk_data.get("usage") is not None:
            self.usage = map_usage(event.chunk_data["usage"])

    @staticmethod
    def _strict_scalar_delta(event: CodeBuddyResponseEvent, field_name: str) -> Optional[str]:
        if field_name not in event.delta:
            return None
        value = event.delta[field_name]
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise UpstreamProtocolViolation(f"CodeBuddy {field_name} delta must be a string")
        return value

    def _tool_state(self, tool_call: Any) -> tuple[_ToolState, str]:
        if not isinstance(tool_call, dict):
            raise UpstreamProtocolViolation("CodeBuddy tool call delta must be an object")
        if "index" in tool_call and (
            not isinstance(tool_call["index"], int) or isinstance(tool_call["index"], bool)
        ):
            raise UpstreamProtocolViolation("CodeBuddy tool call index must be an integer")
        index = self.tool_index_state.resolve(tool_call)
        if index is None:
            raise UpstreamProtocolViolation("CodeBuddy tool call cannot be associated with a stable index")
        state = self.tools.get(index)
        if state is None:
            state = _ToolState(upstream_index=index)
            self.tools[index] = state
        if state.closed:
            raise UpstreamProtocolViolation("CodeBuddy emitted data for a closed tool call")
        argument_delta = state.merge(tool_call)
        return state, argument_delta

    def _tool_calls(self, event: CodeBuddyResponseEvent) -> Optional[List[Any]]:
        if "tool_calls" not in event.delta:
            return None
        value = event.raw_tool_calls
        if value in (None, []):
            return None
        if not isinstance(value, list):
            raise UpstreamProtocolViolation("CodeBuddy tool_calls delta must be an array")
        return value

    def _require_complete(self) -> None:
        if self.finish_reason is None:
            raise UpstreamProtocolViolation("CodeBuddy response is missing finish_reason")
        if self.usage is None:
            if self.finish_reason == "refusal":
                self.usage = {"input_tokens": 0, "output_tokens": 0}
                return
            raise UpstreamProtocolViolation("CodeBuddy response is missing usage")


class AnthropicNonStreamAggregator(_AnthropicEventConsumer):
    """严格聚合 CodeBuddy SSE 为单个 Anthropic Message。"""

    def __init__(self, context: AnthropicResponseContext):
        super().__init__()
        self.context = context
        self.blocks: List[Dict[str, Any]] = []

    def _append_scalar(self, block_type: str, key: str, value: str) -> None:
        if self.blocks and self.blocks[-1].get("type") == block_type:
            self.blocks[-1][key] += value
            return
        self.blocks.append({"type": block_type, key: value})

    def _process_tools(self, tool_calls: List[Any]) -> None:
        for tool_call in tool_calls:
            state, _argument_delta = self._tool_state(tool_call)
            block = next(
                (item for item in self.blocks if item.get("_tool_index") == state.upstream_index),
                None,
            )
            if block is None:
                self.blocks.append({"type": "tool_use", "_tool_index": state.upstream_index})

    def process_event(self, event: CodeBuddyResponseEvent) -> None:
        self._capture_finish_and_usage(event)
        reasoning = self._strict_scalar_delta(event, "reasoning_content")
        content = self._strict_scalar_delta(event, "content")
        tool_calls = self._tool_calls(event)
        if reasoning is not None:
            self._append_scalar("thinking", "thinking", reasoning)
        if content is not None:
            self._append_scalar("text", "text", content)
        if tool_calls is not None:
            self._process_tools(tool_calls)

    def _final_blocks(self) -> List[Dict[str, Any]]:
        blocks = copy_blocks = [item.copy() for item in self.blocks]
        run_start = 0
        while run_start < len(blocks):
            if "_tool_index" not in blocks[run_start]:
                run_start += 1
                continue
            run_end = run_start
            while run_end < len(blocks) and "_tool_index" in blocks[run_end]:
                run_end += 1
            blocks[run_start:run_end] = sorted(
                blocks[run_start:run_end],
                key=lambda item: item["_tool_index"],
            )
            run_start = run_end

        for block in blocks:
            if block["type"] == "thinking":
                block["signature"] = anthropic_thinking_signature(block["thinking"])
            if "_tool_index" in block:
                state = self.tools[block.pop("_tool_index")]
                block.update({"id": state.tool_id, "name": state.name, "input": state.parsed_input()})
        return copy_blocks

    def finalize(self) -> Dict[str, Any]:
        self._require_complete()
        return {
            "id": self.context.message_id,
            "type": "message",
            "role": "assistant",
            "model": self.context.model,
            "content": self._final_blocks(),
            "stop_reason": self.finish_reason,
            "stop_sequence": None,
            "usage": self.usage,
        }


@dataclass
class _OpenScalarBlock:
    block_type: str
    content_index: int
    value: str = ""


class AnthropicStreamEncoder(_AnthropicEventConsumer):
    """把中立事件编码成严格有序的 Anthropic 具名 SSE。"""

    def __init__(self, context: AnthropicResponseContext):
        super().__init__()
        self.context = context
        self.started = False
        self.finished = False
        self.next_content_index = 0
        self.scalar: Optional[_OpenScalarBlock] = None

    def _message_start(self) -> str:
        return format_anthropic_sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self.context.message_id,
                "type": "message",
                "role": "assistant",
                "model": self.context.model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

    def _ensure_started(self, chunks: List[str]) -> None:
        if not self.started:
            chunks.append(self._message_start())
            self.started = True

    def _close_scalar(self) -> List[str]:
        if self.scalar is None:
            return []
        chunks: List[str] = []
        if self.scalar.block_type == "thinking":
            chunks.append(format_anthropic_sse("content_block_delta", {
                "type": "content_block_delta",
                "index": self.scalar.content_index,
                "delta": {
                    "type": "signature_delta",
                    "signature": anthropic_thinking_signature(self.scalar.value),
                },
            }))
        chunks.append(format_anthropic_sse("content_block_stop", {
            "type": "content_block_stop",
            "index": self.scalar.content_index,
        }))
        self.scalar = None
        return chunks

    def _close_tools(self) -> List[str]:
        chunks: List[str] = []
        active = sorted(
            (state for state in self.tools.values() if state.started and not state.closed),
            key=lambda state: state.content_index,
        )
        for state in active:
            state.parsed_input()
            chunks.append(format_anthropic_sse("content_block_stop", {
                "type": "content_block_stop",
                "index": state.content_index,
            }))
            state.closed = True
        return chunks

    def _emit_scalar(self, block_type: str, value: str) -> List[str]:
        chunks: List[str] = []
        chunks.extend(self._close_tools())
        if self.scalar is None or self.scalar.block_type != block_type:
            chunks.extend(self._close_scalar())
            scalar = _OpenScalarBlock(block_type, self.next_content_index)
            self.next_content_index += 1
            self.scalar = scalar
            initial_block = {"type": block_type, "thinking": ""} if block_type == "thinking" else {"type": "text", "text": ""}
            chunks.append(format_anthropic_sse("content_block_start", {
                "type": "content_block_start",
                "index": scalar.content_index,
                "content_block": initial_block,
            }))
        self.scalar.value += value
        delta = (
            {"type": "thinking_delta", "thinking": value}
            if block_type == "thinking"
            else {"type": "text_delta", "text": value}
        )
        chunks.append(format_anthropic_sse("content_block_delta", {
            "type": "content_block_delta",
            "index": self.scalar.content_index,
            "delta": delta,
        }))
        return chunks

    def _process_tools(self, tool_calls: List[Any]) -> List[str]:
        chunks = self._close_scalar()
        for tool_call in tool_calls:
            state, argument_delta = self._tool_state(tool_call)
            if not state.started and state.tool_id is not None and state.name is not None:
                state.content_index = self.next_content_index
                self.next_content_index += 1
                state.started = True
                chunks.append(format_anthropic_sse("content_block_start", {
                    "type": "content_block_start",
                    "index": state.content_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": state.tool_id,
                        "name": state.name,
                        "input": {},
                    },
                }))
                if state.arguments:
                    chunks.append(format_anthropic_sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": state.content_index,
                        "delta": {"type": "input_json_delta", "partial_json": state.arguments},
                    }))
            elif state.started and argument_delta:
                chunks.append(format_anthropic_sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": state.content_index,
                    "delta": {"type": "input_json_delta", "partial_json": argument_delta},
                }))
        return chunks

    def process_event(self, event: CodeBuddyResponseEvent) -> List[str]:
        if self.finished:
            raise UpstreamProtocolViolation("CodeBuddy emitted data after message_stop")
        self._capture_finish_and_usage(event)
        reasoning = self._strict_scalar_delta(event, "reasoning_content")
        content = self._strict_scalar_delta(event, "content")
        tool_calls = self._tool_calls(event)
        chunks: List[str] = []
        self._ensure_started(chunks)
        if reasoning is not None:
            chunks.extend(self._emit_scalar("thinking", reasoning))
        if content is not None:
            chunks.extend(self._emit_scalar("text", content))
        if tool_calls is not None:
            chunks.extend(self._process_tools(tool_calls))
        return chunks

    def finalize(self) -> List[str]:
        if self.finished:
            raise UpstreamProtocolViolation("Anthropic stream was finalized more than once")
        self._require_complete()
        chunks: List[str] = []
        self._ensure_started(chunks)
        chunks.extend(self._close_scalar())
        chunks.extend(self._close_tools())
        for state in self.tools.values():
            if not state.started:
                raise UpstreamProtocolViolation("CodeBuddy tool metadata arrived too late to start a block")
        chunks.append(format_anthropic_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self.finish_reason, "stop_sequence": None},
            "usage": self.usage,
        }))
        chunks.append(format_anthropic_sse("message_stop", {"type": "message_stop"}))
        self.finished = True
        return chunks


class AnthropicDownstreamAdapter:
    """供共享上游传输层调用的 Anthropic 下游响应适配器。"""

    media_type = "text/event-stream"

    def __init__(self, context: AnthropicResponseContext):
        self.context = context

    @property
    def stream_headers(self) -> Dict[str, str]:
        return {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "request-id": self.context.request_id,
        }

    def create_stream_state(self) -> AnthropicStreamEncoder:
        return AnthropicStreamEncoder(self.context)

    @staticmethod
    def process_stream_event(state: AnthropicStreamEncoder, event: Any) -> List[str]:
        if not isinstance(event, CodeBuddyResponseEvent):
            return []
        return state.process_event(event)

    @staticmethod
    def finalize_stream(state: AnthropicStreamEncoder, _upstream_done: bool) -> List[str]:
        return state.finalize()

    def format_stream_error(self, error: Any) -> str:
        status_code, error_type, message = map_anthropic_error(error)
        del status_code
        return format_anthropic_stream_error(self.context.request_id, error_type, message)

    def create_non_stream_aggregator(self) -> AnthropicNonStreamAggregator:
        return AnthropicNonStreamAggregator(self.context)

    @staticmethod
    def process_non_stream_event(
            aggregator: AnthropicNonStreamAggregator,
            event: Any,
    ) -> None:
        if isinstance(event, CodeBuddyResponseEvent):
            aggregator.process_event(event)

    @staticmethod
    def finalize_non_stream(
            aggregator: AnthropicNonStreamAggregator,
            _upstream_done: bool,
    ) -> Dict[str, Any]:
        return aggregator.finalize()


def map_anthropic_error(error: Any) -> tuple[int, str, str]:
    """把共享/上游错误映射为 Anthropic HTTP 状态和错误类型。"""
    message = getattr(error, "message", None) or str(getattr(error, "detail", error))
    category = getattr(error, "error_type", None)
    upstream_status = getattr(error, "upstream_status_code", None)
    status_code = getattr(error, "status_code", 500)

    if upstream_status == 504 or category == "upstream_timeout" or status_code == 504:
        return 504, "timeout_error", message
    if upstream_status == 429 or status_code == 429:
        return 429, "rate_limit_error", message
    if upstream_status == 529:
        return 529, "overloaded_error", message
    if upstream_status == 403 or status_code == 403:
        return 403, "permission_error", message
    if status_code == 413:
        return 413, "request_too_large", message
    if status_code == 404:
        return 404, "not_found_error", message
    if status_code == 400:
        return 400, "invalid_request_error", message
    if category in {"no_credential", "internal_error"} or status_code == 500:
        return 500, "api_error", message
    return 502, "api_error", message
