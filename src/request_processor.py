"""聊天请求验证和上游载荷预处理。"""
import copy
from dataclasses import dataclass
from typing import Any, Dict

from fastapi import HTTPException

from .system_prompt_rewriter import rewrite_system_prompt_messages
from .openai_request import map_token_limit, require_string, validate_content, validate_options
from .openai_errors import OpenAIRequestError


@dataclass(frozen=True)
class PreparedCodeBuddyRequest:
    """分离客户端响应契约与经过策略处理的 CodeBuddy 上游载荷。"""

    payload: Dict[str, Any]
    client_wants_stream: bool
    response_model: str
    client_include_usage: bool = False
    # 仅由服务端准备流程标记，不能信任请求载荷中的同名扩展。
    model_is_configured: bool = False


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

    from config import get_codex_auto_review_model, get_strip_model_namespace

    # 精确别名只映射一次，目标模型再进入统一策略；不改变客户端响应模型名。
    if payload.get("model") == "codex-auto-review":
        payload["model"] = get_codex_auto_review_model(user)

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

    rewrite_system_prompt_messages(payload.get("messages", []))


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
        map_token_limit(payload)
        apply_request_policies(payload, user)
        response_model = str(request_body.get("model") or payload.get("model") or "unknown")
        adapt_openai_payload_for_codebuddy(payload)
        return PreparedCodeBuddyRequest(
            payload=payload,
            client_wants_stream=bool(request_body.get("stream", False)),
            response_model=response_model,
            client_include_usage=(request_body.get("stream_options") or {}).get("include_usage", False),
            model_is_configured=request_body.get("model") == "codex-auto-review",
        )

    @staticmethod
    def validate_request(request_body: Dict[str, Any]) -> None:
        """验证请求参数。"""
        if not isinstance(request_body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        validate_options(request_body)

        messages = request_body.get("messages")
        if not messages or not isinstance(messages, list):
            raise OpenAIRequestError("Messages field is required and must be an array", "messages")

        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise OpenAIRequestError(f"Message {i} must be an object", f"messages[{i}]")
            if "role" not in msg:
                raise OpenAIRequestError(f"Message {i} must have 'role' field", f"messages[{i}].role")
            require_string(msg["role"], f"messages[{i}].role")
            if "content" in msg:
                validate_content(msg["content"], f"messages[{i}].content")
            if "content" not in msg:
                tool_calls = msg.get("tool_calls")
                audio = msg.get("audio")
                refusal = msg.get("refusal")
                if msg["role"] == "assistant" and (
                    isinstance(refusal, str) and bool(refusal)
                    or isinstance(audio, dict) and isinstance(audio.get("id"), str) and bool(audio["id"])
                ):
                    continue
                if msg.get("role") == "assistant" and isinstance(msg.get("function_call"), dict):
                    function = msg["function_call"]
                    require_string(function.get("name"), f"messages[{i}].function_call.name")
                    require_string(function.get("arguments"), f"messages[{i}].function_call.arguments", allow_empty=True)
                    continue
                if (
                    msg.get("role") != "assistant"
                    or not isinstance(tool_calls, list)
                    or not tool_calls
                    or not all(isinstance(tool_call, dict) for tool_call in tool_calls)
                ):
                    raise OpenAIRequestError(f"Message {i} must have 'content' field", f"messages[{i}].content")
