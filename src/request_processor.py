"""聊天请求验证和上游载荷预处理。"""
import copy
from dataclasses import dataclass
from typing import Any, Dict

from fastapi import HTTPException


@dataclass(frozen=True)
class PreparedCodeBuddyRequest:
    """分离客户端响应契约与经过策略处理的 CodeBuddy 上游载荷。"""

    payload: Dict[str, Any]
    client_wants_stream: bool
    response_model: str


def strip_model_namespace(model: Any) -> str:
    return str(model or "").strip().rsplit("/", 1)[-1]


def normalize_model_id(model: Any) -> str:
    return strip_model_namespace(model).lower()


def should_configure_model_reasoning(model: Any, user: Any = None) -> bool:
    from config import get_forced_reasoning_models

    reasoning_models = {
        normalize_model_id(model_id)
        for model_id in get_forced_reasoning_models(user)
    }
    return normalize_model_id(model) in reasoning_models


def forced_reasoning_thinking_options() -> Dict[str, Any]:
    return {"type": "enabled"}


def apply_forced_reasoning_options(payload: Dict[str, Any]) -> None:
    """保持本项目原有策略：对推理模型强制传 max。"""
    payload.pop("enable_thinking", None)  # 去掉 codebuddy 官方的思考开关（不确定这里是否确实应该去除）
    payload["reasoning_effort"] = "max"
    thinking = payload.get("thinking") if isinstance(payload.get("thinking"), dict) else {}
    payload["thinking"] = {**thinking, **forced_reasoning_thinking_options()}


def is_false_like(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "no", "off", "disabled"}
    return False


def is_thinking_explicitly_disabled(payload: Dict[str, Any]) -> bool:
    if "enable_thinking" in payload and is_false_like(payload.get("enable_thinking")):
        return True

    thinking = payload.get("thinking")
    if isinstance(thinking, dict):
        return str(thinking.get("type", "")).strip().lower() == "disabled"

    return False


def apply_default_thinking_options(payload: Dict[str, Any]) -> None:
    if not is_thinking_explicitly_disabled(payload):
        payload["enable_thinking"] = True


def apply_forced_temperature(payload: Dict[str, Any], user: Any = None) -> None:
    from config import get_forced_temperature

    forced_temperature = get_forced_temperature(user)
    if forced_temperature is not None:
        payload["temperature"] = forced_temperature


def apply_request_policies(payload: Dict[str, Any], user: Any = None) -> None:
    """应用用户级模型和消息策略，不处理上游传输约束。"""
    if not payload.get("model"):
        from config import DEFAULT_CODEBUDDY_MODELS, get_available_models

        payload["model"] = next(
            (model for model in get_available_models(user) if model),
            DEFAULT_CODEBUDDY_MODELS[0],
        )

    from config import get_strip_model_namespace

    if get_strip_model_namespace(user):
        payload["model"] = strip_model_namespace(payload.get("model"))
    if should_configure_model_reasoning(payload.get("model"), user):
        apply_forced_reasoning_options(payload)
    else:
        apply_default_thinking_options(payload)
    apply_forced_temperature(payload, user)

    messages = payload.get("messages", [])
    if len(messages) == 1 and messages[0].get("role") == "user":
        system_msg = {"role": "system", "content": "You are a helpful assistant."}
        payload["messages"] = [system_msg] + messages


def adapt_openai_payload_for_codebuddy(payload: Dict[str, Any]) -> None:
    """应用 CodeBuddy 上游只支持流式响应的协议约束。"""
    stream_options = payload.get("stream_options") if isinstance(payload.get("stream_options"), dict) else {}
    payload["stream_options"] = {**stream_options, "include_usage": True}
    payload["stream"] = True


class RequestProcessor:
    """请求预处理器。"""

    @staticmethod
    def prepare_request(request_body: Dict[str, Any], user: Any = None) -> PreparedCodeBuddyRequest:
        """依次应用产品策略和协议适配，同时保留客户端响应契约。"""
        payload = copy.deepcopy(request_body)
        apply_request_policies(payload, user)
        response_model = str(request_body.get("model") or payload.get("model") or "unknown")
        adapt_openai_payload_for_codebuddy(payload)
        return PreparedCodeBuddyRequest(
            payload=payload,
            client_wants_stream=bool(request_body.get("stream", False)),
            response_model=response_model,
        )

    @staticmethod
    def validate_request(request_body: Dict[str, Any]) -> None:
        """验证请求参数。"""
        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        messages = request_body.get("messages")
        if not messages or not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="Messages field is required and must be an array")

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise HTTPException(status_code=400, detail=f"Message {i} must be an object")
            if "role" not in msg:
                raise HTTPException(status_code=400, detail=f"Message {i} must have 'role' field")
            if "content" not in msg:
                tool_calls = msg.get("tool_calls")
                if (
                    msg.get("role") != "assistant"
                    or not isinstance(tool_calls, list)
                    or not tool_calls
                    or not all(isinstance(tool_call, dict) for tool_call in tool_calls)
                ):
                    raise HTTPException(status_code=400, detail=f"Message {i} must have 'content' field")
