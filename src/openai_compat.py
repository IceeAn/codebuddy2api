"""OpenAI 兼容响应转换工具。"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .codebuddy_events import CodeBuddyResponseEvent, ToolCallIndexState


@dataclass(frozen=True)
class CompletionResponseContext:
    """一次完成请求对客户端稳定可见的响应信封。"""

    response_id: str
    created: int
    model: str


def normalize_openai_stream_chunk_envelope(
        chunk_data: Dict[str, Any],
        context: CompletionResponseContext,
) -> Dict[str, Any]:
    """使用客户端响应上下文统一覆盖流式 chunk 信封。"""
    converted_chunk = chunk_data.copy()
    converted_chunk["id"] = context.response_id
    converted_chunk["object"] = "chat.completion.chunk"
    converted_chunk["created"] = context.created
    converted_chunk["model"] = context.model
    return converted_chunk


def _copy_first_choice_with_delta(
        chunk_data: Dict[str, Any],
        delta: Dict[str, Any],
) -> Dict[str, Any]:
    """复制首个 choice 并替换 delta，不修改上游对象。"""
    copied_chunk = chunk_data.copy()
    copied_choices = list(copied_chunk.get("choices", []))
    copied_choice = copied_choices[0].copy()
    copied_choice["delta"] = delta
    copied_choices[0] = copied_choice
    copied_chunk["choices"] = copied_choices
    return copied_chunk


class OpenAIStreamNormalizer:
    """规范化流式 delta 并补齐 assistant 角色。"""

    def __init__(self):
        self.role_sent = False

    def normalize(self, chunk_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        choices = chunk_data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return [chunk_data]

        choice = choices[0]
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            return [chunk_data]
        delta = delta.copy()
        finish_reason = choice.get("finish_reason")
        outgoing_chunks: List[Dict[str, Any]] = []

        role = delta.pop("role", None)
        if not self.role_sent and (role in ("assistant", "") or self._has_assistant_delta(delta)):
            outgoing_chunks.append(self._copy_with_delta(chunk_data, {"role": "assistant"}, None))
            self.role_sent = True

        delta = self._remove_empty_delta_fields(delta)

        if not delta:
            if finish_reason is None and not chunk_data.get("usage"):
                return outgoing_chunks
            outgoing_chunks.append(self._copy_with_delta(chunk_data, {}, finish_reason))
            return outgoing_chunks

        outgoing_chunks.append(self._copy_with_delta(chunk_data, delta, finish_reason))
        return outgoing_chunks

    @staticmethod
    def _has_assistant_delta(delta: Dict[str, Any]) -> bool:
        return any(key in delta for key in ("reasoning_content", "content", "tool_calls"))

    @staticmethod
    def _remove_empty_delta_fields(delta: Dict[str, Any]) -> Dict[str, Any]:
        normalized_delta = delta.copy()
        if normalized_delta.get("reasoning_content") in ("", None):
            normalized_delta.pop("reasoning_content", None)
        if normalized_delta.get("content") in ("", None):
            normalized_delta.pop("content", None)
        if normalized_delta.get("tool_calls") in ([], None):
            normalized_delta.pop("tool_calls", None)
        function_call = normalized_delta.get("function_call")
        if function_call in ({}, None) or (
                isinstance(function_call, dict)
                and not any(value not in ("", None, [], {}) for value in function_call.values())
        ):
            normalized_delta.pop("function_call", None)
        if normalized_delta.get("refusal") in ("", None):
            normalized_delta.pop("refusal", None)
        if normalized_delta.get("extra_fields") in ({}, None):
            normalized_delta.pop("extra_fields", None)
        return normalized_delta

    @staticmethod
    def _copy_with_delta(
            chunk_data: Dict[str, Any],
            delta: Dict[str, Any],
            finish_reason: Optional[str],
    ) -> Dict[str, Any]:
        copied_chunk = _copy_first_choice_with_delta(chunk_data, delta)
        copied_choices = copied_chunk["choices"]
        copied_choice = copied_choices[0]
        copied_choice["finish_reason"] = finish_reason
        return copied_chunk


def add_openai_tool_call_indexes(
        event: CodeBuddyResponseEvent,
        tool_call_index_state: ToolCallIndexState,
) -> Dict[str, Any]:
    """为工具调用补齐 OpenAI 流式 index，并返回转换后的 chunk。"""
    if not event.tool_calls:
        return event.chunk_data

    converted_tool_calls = []
    for tool_call in event.tool_calls:
        if not isinstance(tool_call, dict):
            converted_tool_calls.append(tool_call)
            continue
        converted_tool_call = tool_call.copy()
        index = tool_call_index_state.resolve(tool_call)
        if index is not None:
            converted_tool_call["index"] = index
        converted_tool_calls.append(converted_tool_call)

    converted_delta = event.delta.copy()
    converted_delta["tool_calls"] = converted_tool_calls
    return _copy_first_choice_with_delta(event.chunk_data, converted_delta)
