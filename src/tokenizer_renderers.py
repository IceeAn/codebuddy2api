"""官方模板所需的消息与思考参数适配。"""

import copy
import json
from pathlib import Path

from .request_processor import is_thinking_explicitly_disabled

PROMPTS = json.loads(
    Path(__file__).with_name("tokenizer_prompts.json").read_text(encoding="utf-8")
)


def deep_sort(value):
    if isinstance(value, dict):
        return {key: deep_sort(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [deep_sort(item) for item in value]
    return value


def ordered_tool_results(messages):
    """按最近一次 assistant 工具声明重排连续工具结果。"""
    result, calls, index = [], {}, 0
    while index < len(messages):
        message = copy.deepcopy(messages[index])
        if message["role"] == "assistant":
            calls = {
                call["id"]: (i, call["function"]["name"])
                for i, call in enumerate(message.get("tool_calls", []))
            }
        if message["role"] != "tool":
            result.append(message)
            index += 1
            continue
        group = []
        while index < len(messages) and messages[index]["role"] == "tool":
            group.append(copy.deepcopy(messages[index]))
            index += 1
        if all(item.get("tool_call_id") in calls for item in group):
            group.sort(key=lambda item: calls[item["tool_call_id"]][0])
            for item in group:
                item["tool"] = calls[item["tool_call_id"]][1]
        result.extend(group)
    return result


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _deepseek_messages(payload):
    messages = copy.deepcopy(payload["messages"])
    if payload.get("tools") or payload.get("response_format"):
        if messages[0]["role"] != "system":
            messages.insert(0, {"role": "system", "content": ""})
        if payload.get("tools"):
            messages[0]["tools"] = payload["tools"]
        if payload.get("response_format"):
            messages[0]["response_format"] = (
                payload["response_format"]
                .get("json_schema", {})
                .get("schema", payload["response_format"])
            )
    merged = []
    for message in ordered_tool_results(messages):
        role = message["role"]
        if role in {"user", "tool"}:
            content = message.get("content") or ""
            block = (
                f"<tool_result>{content}</tool_result>" if role == "tool" else content
            )
            if merged and merged[-1]["role"] == "user":
                merged[-1]["parts"].append(block)
            else:
                merged.append({"role": "user", "parts": [block]})
        else:
            merged.append(message)
    return merged


def _ds_calls(calls, encoder):
    space = " " if encoder == "deepseek_v41" else ""
    invoke, parameter = space + "invoke", space + "parameter"
    rendered = []
    for call in calls:
        function = call["function"]
        arguments = function["arguments"]
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        parts = []
        for key, value in arguments.items():
            is_string = isinstance(value, str)
            text = value if is_string else _json(value)
            parts.append(
                f'<｜DSML｜{parameter} name="{key}" string="{str(is_string).lower()}">{text}</｜DSML｜{parameter}>'
            )
        body = "\n".join(parts)
        rendered.append(
            f'<｜DSML｜{invoke} name="{function["name"]}">\n{body}\n</｜DSML｜{invoke}>'
        )
    body = "\n".join(rendered)
    group = " calls" if encoder == "deepseek_v41" else "tool_calls"
    return f"\n\n<｜DSML｜{group}>\n{body}\n</｜DSML｜{group}>"


def _deepseek(payload, encoder):
    prompts = PROMPTS[encoder]
    thinking = not is_thinking_explicitly_disabled(payload)
    messages = _deepseek_messages(payload)
    last_user = max(
        (index for index, message in enumerate(messages) if message["role"] == "user"),
        default=-1,
    )
    drop = payload.get("thinking", {}).get("clear_thinking", True) and not payload.get(
        "tools"
    )
    effort = payload.get("reasoning_effort", "high")
    parts = ["<｜begin▁of▁sentence｜>"]
    if encoder == "deepseek_v41":
        if thinking or messages[0]["role"] == "system":
            parts.append("<｜System｜>")
        if thinking:
            budget = prompts["REASONING_EFFORT_MAPPINGS"].get(
                effort, 100 if effort == "xhigh" else 50
            )
            parts.append(prompts["REASONING_EFFORT_TEMPLATE"].format(budget=budget))
    elif thinking and effort in {"max", "xhigh"}:
        parts.append(prompts["REASONING_EFFORT_MAX"])
    for index, message in enumerate(messages):
        role = message["role"]
        if role == "system":
            if encoder == "deepseek_v41" and index > 0:
                parts.append("<｜System｜>")
            parts.append(message.get("content") or "")
            if message.get("tools"):
                variables = {
                    **prompts,
                    "tool_schemas": "\n".join(
                        _json(tool["function"]) for tool in message["tools"]
                    ),
                    "tc_block_name": prompts.get("tool_calls_block_name", "tool_calls"),
                }
                parts.append("\n\n" + prompts["TOOLS_TEMPLATE"].format(**variables))
            if message.get("response_format"):
                parts.append(
                    "\n\n"
                    + prompts["response_format_template"].format(
                        schema=_json(message["response_format"])
                    )
                )
        elif role == "user":
            parts.append("<｜User｜>" + "\n\n".join(message["parts"]))
        else:
            if thinking and (not drop or index > last_user):
                parts.append((message.get("reasoning_content") or "") + "</think>")
            parts.append(message.get("content") or "")
            if message.get("tool_calls"):
                parts.append(_ds_calls(message["tool_calls"], encoder))
            parts.append("<｜end▁of▁sentence｜>")
        follows_assistant = (
            index == len(messages) - 1 or messages[index + 1]["role"] == "assistant"
        )
        needs_header = role == "user" or (
            encoder == "deepseek_v41" and role == "system" and index > 0
        )
        if follows_assistant and needs_header:
            parts.append("<｜Assistant｜>")
            parts.append(
                "<think>"
                if thinking and (not drop or index >= last_user)
                else "</think>"
            )
    return [("".join(parts), True)]


def _text(text):
    return [(str(text), False)] if text is not None and str(text) else []


def _tag(name, attributes=(), *, closing=False):
    result = [("<|close|>" if closing else "<|open|>", True), (name, False)]
    for key, value in attributes:
        escaped = str(value).replace("&", "&amp;").replace('"', "&quot;")
        result.extend(
            [(f" {key}", False), ('="', False), (escaped, False), ('"', False)]
        )
    return result + [("<|sep|>", True)]


def _end():
    return _tag("message", closing=True) + [("<|end_of_msg|>", True)]


def _internal(kind, text):
    return _tag("message", [("role", "system"), ("type", kind)]) + _text(text) + _end()


def _argument_type(value):
    if value is None:
        return "null"
    return {
        bool: "boolean",
        str: "string",
        int: "number",
        float: "number",
        dict: "object",
        list: "array",
    }[type(value)]


def _arguments(raw):
    """保留 JSON 字符串内每个非字符串值的原始字面量。"""
    if isinstance(raw, dict):
        return [
            (
                key,
                _argument_type(value),
                value if isinstance(value, str) else _json(value),
            )
            for key, value in raw.items()
        ]
    decoder = json.JSONDecoder()
    text = raw.strip()
    if text == "{}":
        return []
    index, result = 1, []
    while index < len(text):
        while text[index].isspace():
            index += 1
        key, index = decoder.raw_decode(text, index)
        while text[index].isspace() or text[index] == ":":
            index += 1
        start = index
        value, index = decoder.raw_decode(text, index)
        result.append(
            (
                key,
                _argument_type(value),
                value if isinstance(value, str) else text[start:index],
            )
        )
        while text[index].isspace():
            index += 1
        if text[index] == "}":
            break
        index += 1
    return result


def _kimi_assistant(message, thinking):
    result = []
    if thinking:
        result += (
            _tag("think")
            + _text(message.get("reasoning_content") or message.get("reasoning"))
            + _tag("think", closing=True)
        )
    result += (
        _tag("response")
        + _text(message.get("content"))
        + _tag("response", closing=True)
    )
    if message.get("tool_calls"):
        result += _tag("tools")
        for index, call in enumerate(message["tool_calls"], 1):
            function = call["function"]
            result += _tag("call", [("tool", function["name"]), ("index", index)])
            for key, kind, value in _arguments(function["arguments"]):
                result += (
                    _tag("argument", [("key", key), ("type", kind)])
                    + _text(value)
                    + _tag("argument", closing=True)
                )
            result += _tag("call", closing=True)
        result += _tag("tools", closing=True)
    return result


def _kimi3(payload):
    thinking = not is_thinking_explicitly_disabled(payload)
    result, tool_index = [], 0
    if payload.get("tools"):
        body = (
            "# Tools\nHere are the available tools, described in JSONSchema.\n\n```json\n"
            + _compact(deep_sort(payload["tools"]))
            + "\n```"
        )
        result += _internal("tool-declare", body)
    effort = payload.get("reasoning_effort")
    if thinking and effort:
        effort = {"medium": "high", "xhigh": "max"}.get(effort, effort)
        result += _internal(
            "thinking-effort",
            "`thinking_effort` guides on how much to think in your thinking channel (not including the response channel), supported values include `low`, `medium`, `high`, and `max`.\n"
            + f"Now the system is invoked with `thinking_effort={effort}`.",
        )
    for message in ordered_tool_results(payload["messages"]):
        role = message["role"]
        attributes = [("role", role)]
        if role == "tool":
            tool_index += 1
            attributes += [("tool", message["tool"]), ("index", tool_index)]
        elif message.get("name"):
            attributes.append(("name", message["name"]))
        result += _tag("message", attributes)
        if role == "assistant":
            tool_index = 0
            result += _kimi_assistant(message, thinking)
        else:
            result += _text(message.get("content"))
        result += _end()
    choice = payload.get("tool_choice")
    if choice in ("required", "none"):
        instruction = (
            "You MUST call tools"
            if choice == "required"
            else "You MUST NOT call any tools"
        )
        result += _internal(
            "tool-choice",
            f"The system is invoked with `tool_choice={choice}`.\n{instruction} in the next message.",
        )
    response_format = payload.get("response_format", {})
    kind = response_format.get("type")
    if kind in {"json_object", "json_schema"}:
        body = f"The system is invoked with `response_format={kind}`.\nYour response must be raw JSON data without markdown code blocks (```json) or any additional formatting."
        if kind == "json_schema":
            body += (
                "\nThe JSON data must match the following schema:\n```json\n"
                + _compact(deep_sort(response_format["json_schema"]["schema"]))
                + "\n```"
            )
        result += _internal("response-format", body)
    return (
        result
        + _tag("message", [("role", "assistant")])
        + _tag("think" if thinking else "response")
    )


def render_segments(payload, encoder):
    if encoder == "kimi_k3":
        return _kimi3(payload)
    return _deepseek(payload, encoder)


def template_arguments(payload, encoder, config):
    messages = copy.deepcopy(payload["messages"])
    for message in messages:
        for call in message.get("tool_calls", []):
            arguments = call["function"]["arguments"]
            if isinstance(arguments, str) and encoder != "kimi_k2":
                call["function"]["arguments"] = json.loads(arguments)
    thinking = not is_thinking_explicitly_disabled(payload)
    options = payload.get("thinking", {})
    kwargs = {
        "messages": messages,
        "tools": payload.get("tools", []),
        "add_generation_prompt": True,
        "enable_thinking": thinking,
        "thinking": thinking,
        "thinking_mode": options.get("type", "enabled") if thinking else "disabled",
    }
    for name in ("bos_token", "eos_token", "pad_token", "unk_token", "token_suffix"):
        value = config.get(name, "")
        kwargs[name] = value.get("content", "") if isinstance(value, dict) else value
    if "reasoning_effort" in payload:
        kwargs["reasoning_effort"] = payload["reasoning_effort"]
    if "clear_thinking" in options:
        kwargs["clear_thinking"] = options["clear_thinking"]
        # Kimi 和 HY4 的官方模板使用不同名称，两者都需保留。
        kwargs["preserve_thinking"] = not options["clear_thinking"]
        kwargs["preserved_thinking"] = not options["clear_thinking"]
    if encoder in {"hy3", "hy4"}:
        kwargs["reasoning_effort"] = (
            (
                "low"
                if encoder == "hy3" and payload.get("reasoning_effort") == "low"
                else "high"
            )
            if thinking
            else "no_think"
        )
    if encoder == "kimi_k2":
        from .tokenizer_typescript import encode_tools

        kwargs["tools"] = deep_sort(kwargs["tools"])
        kwargs["tools_ts_str"] = encode_tools(kwargs["tools"])
    return kwargs
