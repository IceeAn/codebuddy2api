"""聊天请求验证和上游载荷预处理。"""
import copy
from typing import Any, Dict

from fastapi import HTTPException

from .keyword_replacer import apply_keyword_replacement_to_system_message

DEEPSEEK_V4_REASONING_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash"}
GLM_REASONING_MODELS = {"glm-5.1", "glm-5.2"}
REASONING_MODELS = DEEPSEEK_V4_REASONING_MODELS | GLM_REASONING_MODELS


def normalize_model_id(model: Any) -> str:
    return str(model or "").strip().lower().split("/")[-1]


def should_configure_model_reasoning(model: Any) -> bool:
    return normalize_model_id(model) in REASONING_MODELS


def forced_reasoning_thinking_options() -> Dict[str, Any]:
    return {"type": "enabled"}


def apply_forced_reasoning_options(payload: Dict[str, Any]) -> None:
    """保持本项目原有策略：对推理模型强制传 max。"""
    payload.pop("enable_thinking", None)  # 去掉 codebuddy 官方的思考开关（不确定这里是否确实应该去除）
    payload["reasoning_effort"] = "max"
    thinking = payload.get("thinking") if isinstance(payload.get("thinking"), dict) else {}
    payload["thinking"] = {**thinking, **forced_reasoning_thinking_options()}


class RequestProcessor:
    """请求预处理器。"""

    @staticmethod
    def prepare_payload(request_body: Dict[str, Any]) -> Dict[str, Any]:
        """准备上游请求载荷，不修改调用方传入的请求对象。"""
        payload = copy.deepcopy(request_body)
        if not payload.get("model"):
            from config import DEFAULT_CODEBUDDY_MODELS, get_available_models

            payload["model"] = next(
                (model for model in get_available_models() if model),
                DEFAULT_CODEBUDDY_MODELS[0],
            )
        if should_configure_model_reasoning(payload.get("model")):
            apply_forced_reasoning_options(payload)
        payload["temperature"] = 1
        stream_options = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
        payload["stream_options"] = {**stream_options, "include_usage": True}
        payload["stream"] = True  # CodeBuddy 只支持流式请求

        messages = payload.get("messages", [])
        if len(messages) == 1 and messages[0].get("role") == "user":
            system_msg = {"role": "system", "content": "You are a helpful assistant."}
            payload["messages"] = [system_msg] + messages

        for msg in payload.get("messages", []):
            if msg.get("role") == "system":
                msg["content"] = apply_keyword_replacement_to_system_message(msg.get("content"))

        return payload

    @staticmethod
    def validate_request(request_body: Dict[str, Any]) -> None:
        """验证请求参数。"""
        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        messages = request_body.get("messages")
        if not messages or not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="Messages field is required and must be an array")

        if not messages:
            raise HTTPException(status_code=400, detail="At least one message is required")

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise HTTPException(status_code=400, detail=f"Message {i} must be an object")
            if "role" not in msg:
                raise HTTPException(status_code=400, detail=f"Message {i} must have 'role' field")
            if "content" not in msg and not (msg.get("role") == "assistant" and msg.get("tool_calls")):
                raise HTTPException(status_code=400, detail=f"Message {i} must have 'content' field")
