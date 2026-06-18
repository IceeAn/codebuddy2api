"""OpenAI 兼容响应转换工具。"""
import json
from typing import Any, Dict, List, Optional


def ensure_openai_stream_chunk_fields(
        chunk_data: Dict[str, Any],
        stream_id: str,
        created: int,
        model: str,
) -> Dict[str, Any]:
    """补齐 OpenAI 流式 chunk 必需字段。"""
    if not isinstance(chunk_data, dict) or "choices" not in chunk_data:
        return chunk_data

    converted_chunk = chunk_data.copy()
    converted_chunk["id"] = stream_id
    converted_chunk["object"] = "chat.completion.chunk"
    converted_chunk["created"] = created
    if model:
        converted_chunk["model"] = model
    return converted_chunk


class OpenAIStreamNormalizer:
    """规范化流式 delta，避免客户端把每个 reasoning token 识别成独立块。"""

    def __init__(self):
        self.role_sent = False

    def normalize(self, chunk_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        choices = chunk_data.get("choices")
        if not choices or not isinstance(choices, list):
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
        copied_chunk = chunk_data.copy()
        copied_choices = list(copied_chunk.get("choices", []))
        copied_choice = copied_choices[0].copy()
        copied_choice["delta"] = delta
        copied_choice["finish_reason"] = finish_reason
        copied_choices[0] = copied_choice
        copied_chunk["choices"] = copied_choices
        return copied_chunk


class OpenAICompatibilityConverter:
    """将 CodeBuddy 格式转换为 OpenAI 兼容格式。"""

    @staticmethod
    def convert_tool_call_id(codebuddy_id: str) -> str:
        """透传工具调用 ID。真实上游 2.107.0 已使用 OpenAI 风格 call_*。"""
        return codebuddy_id

    @staticmethod
    def convert_sse_chunk_to_openai_format(
            chunk_data: Dict[str, Any],
            tool_call_index_map: Dict[str, int],
    ) -> Dict[str, Any]:
        """将 CodeBuddy SSE chunk 转换为 OpenAI 格式。"""
        if not chunk_data.get("choices"):
            return chunk_data

        choice = chunk_data["choices"][0]
        delta = choice.get("delta", {})
        tool_calls = delta.get("tool_calls", [])

        if not tool_calls:
            return chunk_data

        converted_tool_calls = []
        for tc in tool_calls:
            converted_tc = tc.copy()

            # 保留上游 ID，只补齐 OpenAI 流式客户端需要的 index。
            if tc.get("id"):
                original_id = tc["id"]
                converted_tc["id"] = OpenAICompatibilityConverter.convert_tool_call_id(original_id)

                if original_id not in tool_call_index_map:
                    tool_call_index_map[original_id] = len(tool_call_index_map)

                converted_tc["index"] = tool_call_index_map[original_id]
            elif tool_call_index_map:
                converted_tc["index"] = max(tool_call_index_map.values())

            converted_tool_calls.append(converted_tc)

        converted_chunk = chunk_data.copy()
        converted_choices = list(converted_chunk.get("choices", []))
        converted_choice = converted_choices[0].copy()
        converted_delta = converted_choice.get("delta", {}).copy()
        converted_delta["tool_calls"] = converted_tool_calls
        converted_choice["delta"] = converted_delta
        converted_choices[0] = converted_choice
        converted_chunk["choices"] = converted_choices
        return converted_chunk


def validate_and_fix_tool_call_args(args: str) -> str:
    """验证并修复工具调用参数，兼容上游分块拼接异常。"""
    if not args:
        return "{}"

    args = args.strip()

    if args.count("}{") > 0:
        json_objects = []
        current_obj = ""
        brace_count = 0

        for char in args:
            current_obj += char
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0 and current_obj.strip():
                    try:
                        parsed = json.loads(current_obj.strip())
                        json_objects.append(parsed)
                        current_obj = ""
                    except json.JSONDecodeError:
                        current_obj = ""

        if json_objects:
            return json.dumps(json_objects[0], ensure_ascii=False)

    try:
        json.loads(args)
        return args
    except json.JSONDecodeError:
        if not args.endswith("}") and args.count("{") > args.count("}"):
            args += "}"
        elif not args.endswith("]") and args.count("[") > args.count("]"):
            args += "]"

        try:
            json.loads(args)
            return args
        except json.JSONDecodeError:
            return "{}"
