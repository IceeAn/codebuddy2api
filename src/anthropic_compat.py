"""Anthropic Messages 请求到 CodeBuddy/OpenAI 风格载荷的兼容转换。"""

import base64
import copy
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Set


ANTHROPIC_MODEL_PREFIX = "anthropic/codebuddy/"
SUPPORTED_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProtocolError(ValueError):
    """表示可安全返回为 Anthropic invalid_request_error 的请求错误。"""


def _fail(message: str) -> None:
    raise AnthropicProtocolError(message)


def _is_object(value: Any) -> bool:
    return isinstance(value, dict)


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _non_empty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{path} must be a non-empty string")
    return value


def unwrap_synthetic_anthropic_model_id(model: Any) -> str:
    """解包网关为 Anthropic 模型发现生成的保留前缀。"""
    model_id = _non_empty_string(model, "model")
    if not model_id.startswith(ANTHROPIC_MODEL_PREFIX):
        return model_id
    real_model_id = model_id[len(ANTHROPIC_MODEL_PREFIX):]
    if not real_model_id:
        _fail("model must contain a real model ID after anthropic/codebuddy/")
    return real_model_id


def anthropic_thinking_signature(thinking: str) -> str:
    """生成仅供本服务往返完整性检查的稳定 thinking 签名。"""
    digest = hashlib.sha256(thinking.encode("utf-8")).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"cb2a_{encoded}"


def synthetic_anthropic_model_id(model: str) -> str:
    return f"{ANTHROPIC_MODEL_PREFIX}{model}"


def _text_part(block: Any, path: str) -> Dict[str, str]:
    if not _is_object(block):
        _fail(f"{path} must be an object")
    if block.get("type") not in {"text", "input_text"}:
        _fail(f"{path}.type is not supported")
    return {"type": "text", "text": _non_empty_string(block.get("text"), f"{path}.text")}


def _text_content(value: Any, path: str) -> Any:
    if isinstance(value, str):
        return _non_empty_string(value, path)
    if not isinstance(value, list) or not value:
        _fail(f"{path} must be a non-empty string or text block array")
    return [_text_part(block, f"{path}[{index}]") for index, block in enumerate(value)]


def _system_messages(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        return [{"role": "system", "content": _non_empty_string(value, "system")}]
    if not isinstance(value, list) or not value:
        _fail("system must be a non-empty string or text block array")
    return [
        {"role": "system", "content": [_text_part(block, f"system[{index}]")]}
        for index, block in enumerate(value)
    ]


def _image_part(block: Dict[str, Any], path: str) -> Dict[str, Any]:
    source = block.get("source")
    if not _is_object(source):
        _fail(f"{path}.source must be an object")
    source_type = source.get("type")
    if source_type == "base64":
        media_type = source.get("media_type")
        if media_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            _fail(f"{path}.source.media_type is not supported")
        data = _non_empty_string(source.get("data"), f"{path}.source.data")
        url = f"data:{media_type};base64,{data}"
    elif source_type == "url":
        url = _non_empty_string(source.get("url"), f"{path}.source.url")
    else:
        _fail(f"{path}.source.type is not supported")
    return {"type": "image_url", "image_url": {"url": url}}


def _document_parts(block: Dict[str, Any], path: str) -> List[Dict[str, Any]]:
    citations = block.get("citations")
    if citations is not None:
        if not _is_object(citations):
            _fail(f"{path}.citations must be an object")
        enabled = citations.get("enabled", False)
        if not isinstance(enabled, bool) or enabled:
            _fail(f"{path}.citations.enabled must be false; native citations are not supported")
    header = "[文档开始]"
    for key, label in (("title", "标题"), ("context", "上下文")):
        value = block.get(key)
        if value is not None:
            if not isinstance(value, str):
                _fail(f"{path}.{key} must be a string")
            header += f"\n{label}：{value}"
    source = block.get("source")
    if not _is_object(source):
        _fail(f"{path}.source must be an object")
    source_type = source.get("type")
    if source_type == "text":
        if source.get("media_type") != "text/plain":
            _fail(f"{path}.source.media_type must be text/plain")
        parts = [{"type": "text", "text": _non_empty_string(source.get("data"), f"{path}.source.data")}]
    elif source_type == "content":
        parts = _input_content_parts(source.get("content"), f"{path}.source.content", allow_documents=False)
    else:
        _fail(f"{path}.source.type is not supported")
    return [{"type": "text", "text": header}, *parts, {"type": "text", "text": "[文档结束]"}]


def _input_block_parts(block: Any, path: str, *, allow_documents: bool = True) -> List[Dict[str, Any]]:
    if not _is_object(block):
        _fail(f"{path} must be an object")
    block_type = block.get("type")
    if block_type in ("text", "input_text"):
        return [_text_part(block, path)]
    if block_type == "image":
        return [_image_part(block, path)]
    if block_type == "document" and allow_documents:
        return _document_parts(block, path)
    _fail(f"{path}.type is not supported")


def _input_content_parts(value: Any, path: str, *, allow_documents: bool = True) -> List[Dict[str, Any]]:
    """仅转换已知语义，媒体始终留在原消息角色中。"""
    if isinstance(value, str):
        return [{"type": "text", "text": _non_empty_string(value, path)}]
    if not isinstance(value, list) or not value:
        _fail(f"{path} must be a non-empty string or content block array")
    parts: List[Dict[str, Any]] = []
    for index, block in enumerate(value):
        parts.extend(_input_block_parts(block, f"{path}[{index}]", allow_documents=allow_documents))
    return parts


def _translate_output_options(body: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    top_k = body.get("top_k")
    if top_k is not None:
        if not _is_integer(top_k) or top_k < 0:
            _fail("top_k must be a non-negative integer")
        result["top_k"] = top_k
    config = body.get("output_config")
    if config is None:
        return result
    if not _is_object(config):
        _fail("output_config must be an object")
    effort = config.get("effort")
    if effort is not None:
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            _fail("output_config.effort is not supported")
        result["reasoning_effort"] = effort
    output_format = config.get("format")
    if output_format is not None:
        if not _is_object(output_format) or output_format.get("type") != "json_schema":
            _fail("output_config.format must be a json_schema object")
        schema = output_format.get("schema")
        if not _is_object(schema):
            _fail("output_config.format.schema must be an object")
        result["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "anthropic_response", "strict": True, "schema": copy.deepcopy(schema),
        }}
    return result


def _tool_definition(tool: Any, index: int) -> Dict[str, Any]:
    path = f"tools[{index}]"
    if not _is_object(tool):
        _fail(f"{path} must be an object")
    if tool.get("type", "custom") != "custom":
        _fail(f"{path}.type must be custom")
    name = _non_empty_string(tool.get("name"), f"{path}.name")
    schema = tool.get("input_schema")
    if not _is_object(schema):
        _fail(f"{path}.input_schema must be an object")

    function: Dict[str, Any] = {"name": name, "parameters": copy.deepcopy(schema)}
    strict = tool.get("strict")
    if strict is not None:
        if not isinstance(strict, bool):
            _fail(f"{path}.strict must be a boolean")
        function["strict"] = strict
    description = tool.get("description")
    if description is not None and not isinstance(description, str):
        _fail(f"{path}.description must be a string")

    examples = tool.get("input_examples")
    if examples is not None:
        if not isinstance(examples, list) or not all(_is_object(item) for item in examples):
            _fail(f"{path}.input_examples must be an array of objects")
        serialized = [
            json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            for item in examples
        ]
        suffix = "Input examples:" + ("\n" + "\n".join(serialized) if serialized else "")
        description = f"{description}\n\n{suffix}" if description is not None else suffix

    if description is not None:
        function["description"] = description
    return {"type": "function", "function": function}


def _translate_tools(value: Any) -> tuple[List[Dict[str, Any]], Set[str]]:
    if not isinstance(value, list):
        _fail("tools must be an array")
    tools = [_tool_definition(tool, index) for index, tool in enumerate(value)]
    names = [tool["function"]["name"] for tool in tools]
    if len(names) != len(set(names)):
        _fail("tool names must be unique")
    return tools, set(names)


def _translate_tool_choice(value: Any, tool_names: Set[str]) -> Dict[str, Any]:
    if not _is_object(value):
        _fail("tool_choice must be an object")
    choice_type = value.get("type")
    name = value.get("name")
    if choice_type == "auto":
        if name is not None:
            _fail("tool_choice.name is only valid for type=tool")
        mapped: Any = "auto"
    elif choice_type == "any":
        if name is not None:
            _fail("tool_choice.name is only valid for type=tool")
        mapped = "required"
    elif choice_type == "none":
        if name is not None:
            _fail("tool_choice.name is only valid for type=tool")
        mapped = "none"
    elif choice_type == "tool":
        selected = _non_empty_string(name, "tool_choice.name")
        if selected not in tool_names:
            _fail("tool_choice references an unknown tool")
        mapped = {"type": "function", "function": {"name": selected}}
    else:
        _fail("tool_choice.type is not supported")

    disable_parallel = value.get("disable_parallel_tool_use")
    if disable_parallel is not None and not isinstance(disable_parallel, bool):
        _fail("tool_choice.disable_parallel_tool_use must be a boolean")
    result = {"tool_choice": mapped}
    if disable_parallel is not None:
        result["parallel_tool_calls"] = not disable_parallel
    return result


def _translate_thinking(value: Any, max_tokens: int) -> Dict[str, Any]:
    if not _is_object(value):
        _fail("thinking must be an object")
    thinking_type = value.get("type")
    budget = value.get("budget_tokens")
    if thinking_type == "adaptive":
        if budget is not None:
            _fail("thinking.budget_tokens is not valid for adaptive thinking")
        return {"thinking": {"type": "enabled"}, "enable_thinking": True}
    if thinking_type == "disabled":
        if budget is not None:
            _fail("thinking.budget_tokens is not valid for disabled thinking")
        return {"thinking": {"type": "disabled"}, "enable_thinking": False}
    if thinking_type == "enabled":
        if not _is_integer(budget) or budget < 1024:
            _fail("thinking.budget_tokens must be an integer of at least 1024")
        if budget >= max_tokens:
            _fail("thinking.budget_tokens must be less than max_tokens")
        return {
            "thinking": {"type": "enabled", "budget_tokens": budget},
            "enable_thinking": True,
        }
    _fail("thinking.type is not supported")


def _assistant_message(
        content: Any,
        path: str,
        known_tool_ids: Set[str],
) -> Dict[str, Any]:
    if isinstance(content, str):
        return {"role": "assistant", "content": _non_empty_string(content, f"{path}.content")}
    if not isinstance(content, list) or not content:
        _fail(f"{path}.content must be a non-empty string or content block array")

    text_parts: List[Dict[str, str]] = []
    tool_calls: List[Dict[str, Any]] = []
    reasoning_parts: List[str] = []
    for block_index, block in enumerate(content):
        block_path = f"{path}.content[{block_index}]"
        if not _is_object(block):
            _fail(f"{block_path} must be an object")
        block_type = block.get("type")
        if block_type in {"text", "input_text"}:
            text_parts.append(_text_part(block, block_path))
            continue
        if block_type == "tool_use":
            tool_id = _non_empty_string(block.get("id"), f"{block_path}.id")
            name = _non_empty_string(block.get("name"), f"{block_path}.name")
            tool_input = block.get("input")
            if not _is_object(tool_input):
                _fail(f"{block_path}.input must be an object")
            if tool_id in known_tool_ids:
                _fail(f"{block_path}.id is duplicated")
            known_tool_ids.add(tool_id)
            tool_calls.append({
                "id": tool_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        tool_input,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            })
            continue
        if block_type == "thinking":
            thinking = _non_empty_string(block.get("thinking"), f"{block_path}.thinking")
            signature = _non_empty_string(block.get("signature"), f"{block_path}.signature")
            if not signature.startswith("cb2a_") or signature != anthropic_thinking_signature(thinking):
                _fail(f"{block_path}.signature is not a valid codebuddy2api thinking signature")
            reasoning_parts.append(thinking)
            continue
        if block_type == "redacted_thinking":
            _fail("redacted_thinking is not supported")
        _fail(f"{block_path}.type is not supported")

    message: Dict[str, Any] = {"role": "assistant"}
    if text_parts:
        message["content"] = text_parts
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    return message


def _tool_result_content(value: Any, path: str) -> Any:
    if value == "" or value == []:
        return ""
    if isinstance(value, str):
        return value
    return _input_content_parts(value, path)


def _user_messages(content: Any, path: str, known_tool_ids: Set[str]) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        return [{"role": "user", "content": _non_empty_string(content, f"{path}.content")}]
    if not isinstance(content, list) or not content:
        _fail(f"{path}.content must be a non-empty string or content block array")

    messages: List[Dict[str, Any]] = []
    content_parts: List[Dict[str, Any]] = []
    for block_index, block in enumerate(content):
        block_path = f"{path}.content[{block_index}]"
        if not _is_object(block):
            _fail(f"{block_path} must be an object")
        block_type = block.get("type")
        if block_type != "tool_result":
            content_parts.extend(_input_block_parts(block, block_path))
            continue
        if content_parts:
            _fail("tool_result blocks must appear before ordinary content blocks in a user message")
        tool_id = _non_empty_string(block.get("tool_use_id"), f"{block_path}.tool_use_id")
        if tool_id not in known_tool_ids:
            _fail(f"{block_path}.tool_use_id references an unknown tool")
        result_content = _tool_result_content(
            block.get("content", ""),
            f"{block_path}.content",
        )
        is_error = block.get("is_error", False)
        if not isinstance(is_error, bool):
            _fail(f"{block_path}.is_error must be a boolean")
        if is_error:
            if isinstance(result_content, str):
                result_content = f"[tool_error]\n{result_content}"
            else:
                if result_content[0]["type"] == "text":
                    result_content[0]["text"] = f"[tool_error]\n{result_content[0]['text']}"
                else:
                    result_content.insert(0, {"type": "text", "text": "[tool_error]\n"})
        messages.append({"role": "tool", "tool_call_id": tool_id, "content": result_content})
    if content_parts:
        messages.append({"role": "user", "content": content_parts})
    return messages


def _translate_messages(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or not value:
        _fail("messages must be a non-empty array")
    translated: List[Dict[str, Any]] = []
    known_tool_ids: Set[str] = set()
    for index, message in enumerate(value):
        path = f"messages[{index}]"
        if not _is_object(message):
            _fail(f"{path} must be an object")
        role = message.get("role")
        if role not in {"user", "assistant", "system"}:
            _fail(f"{path}.role must be user, assistant, or system")
        if "content" not in message:
            _fail(f"{path}.content is required")
        if role == "assistant":
            translated.append(_assistant_message(message["content"], path, known_tool_ids))
        elif role == "user":
            translated.extend(_user_messages(message["content"], path, known_tool_ids))
        else:
            translated.append({
                "role": "system",
                "content": _text_content(message["content"], f"{path}.content"),
            })
    return translated


def translate_anthropic_request(request_body: Any) -> Dict[str, Any]:
    """校验可转换语义，忽略未知字段并返回新的 CodeBuddy/OpenAI 风格对象。"""
    if not _is_object(request_body):
        _fail("request body must be a JSON object")
    for required in ("model", "max_tokens", "messages"):
        if required not in request_body:
            _fail(f"{required} is required")

    max_tokens = request_body["max_tokens"]
    if not _is_integer(max_tokens) or max_tokens < 0:
        _fail("max_tokens must be a non-negative integer")
    translated: Dict[str, Any] = {
        "model": unwrap_synthetic_anthropic_model_id(request_body["model"]),
        "max_tokens": max_tokens,
    }
    translated.update(_translate_output_options(request_body))

    messages = _translate_messages(request_body["messages"])
    if "system" in request_body:
        messages = _system_messages(request_body["system"]) + messages
    translated["messages"] = messages

    stream = request_body.get("stream")
    if stream is not None:
        if not isinstance(stream, bool):
            _fail("stream must be a boolean")
        translated["stream"] = stream
    for field in ("temperature", "top_p"):
        if field in request_body:
            value = request_body[field]
            if not _is_number(value):
                _fail(f"{field} must be a finite number")
            translated[field] = value

    if "stop_sequences" in request_body:
        stop_sequences = request_body["stop_sequences"]
        if (
            not isinstance(stop_sequences, list)
            or not all(isinstance(item, str) and item for item in stop_sequences)
        ):
            _fail("stop_sequences must be an array of non-empty strings")
        if stop_sequences:
            _fail("non-empty stop_sequences are not supported by the CodeBuddy gateway")

    metadata = request_body.get("metadata")
    if metadata is not None and not _is_object(metadata):
        _fail("metadata must be an object")

    tool_names: Set[str] = set()
    if "tools" in request_body:
        tools, tool_names = _translate_tools(request_body["tools"])
        if tools:
            translated["tools"] = tools
    if "tool_choice" in request_body:
        translated.update(_translate_tool_choice(request_body["tool_choice"], tool_names))
    if "thinking" in request_body:
        translated.update(_translate_thinking(request_body["thinking"], max_tokens))
    return translated
