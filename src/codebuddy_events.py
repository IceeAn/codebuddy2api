"""CodeBuddy SSE 的协议中立单 choice 事件与工具关联状态。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


SSE_DONE = object()


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
    """流式和非流式下游适配器共享的宽松上游响应语义。"""

    chunk_data: Dict[str, Any]
    choice: Optional[Dict[str, Any]]
    delta: Dict[str, Any]
    finish_reason: Optional[str]

    @classmethod
    def parse(cls, chunk_data: Dict[str, Any]) -> "CodeBuddyResponseEvent":
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
    def raw_tool_calls(self) -> Any:
        return self.delta.get("tool_calls")

    @property
    def usage(self) -> Any:
        return self.chunk_data.get("usage")
