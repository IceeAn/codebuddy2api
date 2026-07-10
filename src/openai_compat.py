"""OpenAI 兼容响应转换工具。"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CompletionResponseContext:
    """一次完成请求对客户端稳定可见的响应信封。"""

    response_id: str
    created: int
    model: str


@dataclass
class ToolCallIndexState:
    """优先沿用上游 index，仅在缺失时补齐稳定位置。"""

    id_to_index: Dict[str, int] = field(default_factory=dict)
    used_indexes: set[int] = field(default_factory=set)
    current_index: Optional[int] = None

    def resolve(self, tool_call: Dict[str, Any]) -> Optional[int]:
        tool_id = tool_call.get("id")
        upstream_index = tool_call.get("index")

        if isinstance(upstream_index, int) and not isinstance(upstream_index, bool):
            self.used_indexes.add(upstream_index)
            if isinstance(tool_id, str) and tool_id:
                self.id_to_index[tool_id] = upstream_index
            self.current_index = upstream_index
            return upstream_index

        if isinstance(tool_id, str) and tool_id:
            existing_index = self.id_to_index.get(tool_id)
            if existing_index is not None:
                self.current_index = existing_index
                return existing_index

            generated_index = 0
            while generated_index in self.used_indexes:
                generated_index += 1
            self.id_to_index[tool_id] = generated_index
            self.used_indexes.add(generated_index)
            self.current_index = generated_index
        return self.current_index


@dataclass(frozen=True)
class CodeBuddyResponseEvent:
    """流式和非流式路径共享的单 choice 上游响应语义。"""

    chunk_data: Dict[str, Any]
    choice: Optional[Dict[str, Any]]
    delta: Dict[str, Any]
    finish_reason: Optional[str]

    @classmethod
    def parse(cls, chunk_data: Dict[str, Any]) -> "CodeBuddyResponseEvent":
        """提取转换所需的首个 choice，不验证上游协议。"""
        choices = chunk_data.get("choices")
        choice = (
            choices[0]
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else None
        )
        raw_delta = choice.get("delta") if choice is not None else None
        delta = raw_delta if isinstance(raw_delta, dict) else {}
        raw_finish_reason = choice.get("finish_reason") if choice is not None else None
        finish_reason = (
            raw_finish_reason.strip()
            if isinstance(raw_finish_reason, str) and raw_finish_reason.strip()
            else None
        )
        return cls(chunk_data=chunk_data, choice=choice, delta=delta, finish_reason=finish_reason)

    @property
    def has_choice(self) -> bool:
        return self.choice is not None

    @property
    def reasoning_content(self) -> Any:
        return self.delta.get("reasoning_content")

    @property
    def content(self) -> Any:
        return self.delta.get("content")

    @property
    def tool_calls(self) -> Any:
        tool_calls = self.delta.get("tool_calls")
        return tool_calls if isinstance(tool_calls, list) else []

    @property
    def usage(self) -> Any:
        return self.chunk_data.get("usage")


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
