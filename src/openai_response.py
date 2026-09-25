"""把单个 choice 的 CodeBuddy SSE 聚合为 OpenAI 响应。"""
import base64
import binascii
import copy
from typing import Any, Dict, List

from .codebuddy_events import CodeBuddyResponseEvent, ToolCallIndexState, UpstreamProtocolViolation
from .openai_compat import CompletionResponseContext


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
        self.content_seen = False
        self.message_fields: Dict[str, Any] = {}
        self.logprobs = None
        self.audio_bytes = bytearray()
        self.service_tier = None

    def process_event(self, event: CodeBuddyResponseEvent):
        """处理共享上游响应语义事件。"""
        obj = event.chunk_data
        if obj.get("service_tier") is not None:
            self.service_tier = obj["service_tier"]
        self.data["system_fingerprint"] = obj.get("system_fingerprint") or self.data["system_fingerprint"]

        if event.usage:
            self.data["usage"] = event.usage

        if not event.has_choice:
            return
        if isinstance(event.content, str):
            self.content_seen = True
        self._process_message_fields(event.delta)
        self._process_logprobs(event.choice.get("logprobs"))
        if isinstance(event.reasoning_content, str) and event.reasoning_content:
            self.data["reasoning_content"] += event.reasoning_content

        if isinstance(event.content, str) and event.content:
            self.data["content"] += event.content

        if event.tool_calls:
            self._process_tool_calls(event.tool_calls)

        if event.finish_reason:
            self.data["finish_reason"] = event.finish_reason

    def _process_message_fields(self, delta: dict) -> None:
        refusal = delta.get("refusal")
        if isinstance(refusal, str):
            self.message_fields["refusal"] = self.message_fields.get("refusal", "") + refusal
        annotations = delta.get("annotations")
        if isinstance(annotations, list):
            self.message_fields.setdefault("annotations", []).extend(copy.deepcopy(annotations))
        legacy = delta.get("function_call")
        if isinstance(legacy, dict) and any(
            isinstance(legacy.get(field), str) and legacy[field]
            for field in ("name", "arguments")
        ):
            current = self.message_fields.setdefault("function_call", {"name": "", "arguments": ""})
            self._merge_call(current, legacy, "arguments")
        audio = delta.get("audio")
        if isinstance(audio, dict) and audio:
            current = self.message_fields.setdefault("audio", {})
            for field in ("id", "expires_at"):
                if audio.get(field) is not None:
                    current[field] = audio[field]
            if isinstance(audio.get("transcript"), str):
                current["transcript"] = current.get("transcript", "") + audio["transcript"]
            if "data" in audio:
                try:
                    self.audio_bytes.extend(base64.b64decode(audio["data"], validate=True))
                except (ValueError, TypeError, binascii.Error) as error:
                    raise UpstreamProtocolViolation("Invalid upstream audio base64 data") from error
                current["data"] = ""

    def _process_logprobs(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        if self.logprobs is None:
            self.logprobs = {}
        for field in ("content", "refusal"):
            parts = value.get(field)
            if isinstance(parts, list):
                if self.logprobs.get(field) is None:
                    self.logprobs[field] = []
                self.logprobs[field].extend(copy.deepcopy(parts))
            elif field not in self.logprobs:
                self.logprobs[field] = None

    @staticmethod
    def _merge_call(current: dict, part: dict, text_field: str) -> None:
        if isinstance(part.get("name"), str) and part["name"]:
            current["name"] = part["name"]
        if isinstance(part.get(text_field), str) and part[text_field]:
            current[text_field] += part[text_field]

    def _process_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> None:
        """按显式 index、ID 或最近上下文聚合工具调用分块。"""
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            index = self.tool_call_index_state.resolve(tc)
            if index is None:
                continue
            current = self.tool_call_map.get(index)
            if not any(key in tc for key in ("custom", "function")):
                if current is not None:
                    if tc.get("id"):
                        current["id"] = tc["id"]
                    continue
                kind = tc.get("type")
                if kind not in ("custom", "function"):
                    continue
                function = {}
            else:
                kind = "custom" if "custom" in tc else "function"
                function = tc.get(kind)
            text_field = "input" if kind == "custom" else "arguments"
            if not isinstance(function, dict):
                continue
            tool_id = tc.get("id")
            if current is None:
                current = {
                    "id": tool_id,
                    "type": kind,
                    kind: {"name": "", text_field: ""},
                }
                self.tool_call_map[index] = current
            elif tool_id:
                current["id"] = tool_id

            if current["type"] != kind:
                raise UpstreamProtocolViolation("Upstream tool call changed type")
            self._merge_call(current[kind], function, text_field)

    def finalize(self) -> Dict[str, Any]:
        """完成聚合并返回最终非流式响应。"""
        indexes = sorted(self.tool_call_map)
        tool_calls = [self.tool_call_map[index] for index in indexes]

        non_text = tool_calls or any(key in self.message_fields for key in ("refusal", "audio", "function_call"))
        content = None if non_text and not self.content_seen else self.data["content"]
        final_message = {"role": "assistant", "content": content, **copy.deepcopy(self.message_fields)}
        if "data" in final_message.get("audio", {}):
            final_message["audio"]["data"] = base64.b64encode(self.audio_bytes).decode("ascii")
        if self.data["reasoning_content"]:
            final_message["reasoning_content"] = self.data["reasoning_content"]
        if tool_calls:
            final_message["tool_calls"] = tool_calls

        inferred = "function_call" if "function_call" in self.message_fields else "stop"
        finish_reason = self.data["finish_reason"] or ("tool_calls" if tool_calls else inferred)

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
                    "logprobs": copy.deepcopy(self.logprobs),
                }
            ],
        }

        if self.data["usage"]:
            final_response["usage"] = self.data["usage"]
        if self.data["system_fingerprint"]:
            final_response["system_fingerprint"] = self.data["system_fingerprint"]
        if self.service_tier is not None:
            final_response["service_tier"] = self.service_tier

        return final_response
