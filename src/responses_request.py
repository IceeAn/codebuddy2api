"""Responses 无状态请求到 CodeBuddy 消息和工具的转换。"""
import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass

from .openai_request import require, require_string, validate_options


def object_at(value, path):
    require(isinstance(value, dict), path, "must be an object")
    return value


def string_at(value, path, *, empty=False):
    require_string(value, path, allow_empty=empty)
    return value


def array_at(value, path):
    require(isinstance(value, list), path, "must be an array")
    return value


def tool_alias(name, namespace=None):
    """命名空间工具使用稳定别名，历史回放不依赖声明顺序。"""
    if namespace is None:
        return name
    encoded = json.dumps([namespace, name], ensure_ascii=False).encode("utf-8")
    return "cbns_" + hashlib.sha256(encoded).hexdigest()[:48]


@dataclass(frozen=True)
class ToolBinding:
    kind: str
    name: str
    namespace: str | None = None

    @property
    def alias(self):
        return tool_alias(self.name, self.namespace)


def binding_for(value, path, kind):
    name = string_at(value.get("name"), path + ".name")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is not None, path + ".name", "must be a valid tool name")
    namespace = value.get("namespace")
    if namespace is not None:
        string_at(namespace, path + ".namespace")
    return ToolBinding(kind, name, namespace)


def content_parts(value, path):
    if isinstance(value, str):
        return value
    parts = []
    for index, raw in enumerate(array_at(value, path)):
        part_path = f"{path}[{index}]"
        part = object_at(raw, part_path)
        kind = part.get("type")
        if kind in ("input_text", "output_text", "refusal"):
            key = "refusal" if kind == "refusal" else "text"
            parts.append({"type": "text", "text": string_at(part.get(key), part_path + "." + key, empty=True)})
        else:
            require(kind == "input_image", part_path + ".type", "is not supported")
            require(part.get("file_id") is None, part_path + ".file_id", "is not supported")
            url = string_at(part.get("image_url"), part_path + ".image_url")
            detail = part.get("detail", "auto")
            require(detail in ("auto", "low", "high", "original"), part_path + ".detail", "is not supported")
            # original 是 Codex 的原图偏好；CodeBuddy 的最高精度枚举为 high。
            parts.append({"type": "image_url", "image_url": {"url": url, "detail": "high" if detail == "original" else detail}})
    return parts or ""


class _Translator:
    def __init__(self):
        self.bindings = {}
        self.tools = {}
        self.messages = []
        self.calls = {}
        self.results = set()

    def add_tools(self, values, path="tools", namespace=None, description=""):
        for index, raw in enumerate(array_at(values, path)):
            location = f"{path}[{index}]"
            tool = object_at(raw, location)
            kind = tool.get("type")
            desc = string_at(tool.get("description", ""), location + ".description", empty=True)
            if kind == "namespace":
                require(namespace is None, location, "nested namespaces are not supported")
                name = string_at(tool.get("name"), location + ".name")
                self.add_tools(tool.get("tools"), location + ".tools", name, desc)
                continue
            require(kind in ("function", "custom", "tool_search"), location + ".type", "is not supported")
            if kind == "tool_search":
                require(tool.get("execution") == "client", location + ".execution", "must be client")
                require(namespace is None, location, "tool_search cannot be namespaced")
                binding = ToolBinding(kind, "cb2a_tool_search")
                params = object_at(tool.get("parameters", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}), location + ".parameters")
            else:
                binding = binding_for({**tool, "namespace": namespace}, location, kind)
                if kind == "custom":
                    params = {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"], "additionalProperties": False}
                    desc += "\n将完整的原始工具输入保存在 input 字符串中，保留换行和所有字符。"
                    fmt = tool.get("format")
                    if fmt is not None:
                        object_at(fmt, location + ".format")
                        require(fmt.get("type") in ("text", "grammar"), location + ".format.type", "is not supported")
                        if fmt["type"] == "grammar":
                            require(fmt.get("syntax") in ("lark", "regex"), location + ".format.syntax", "is not supported")
                            string_at(fmt.get("definition"), location + ".format.definition")
                        desc += "\n输入格式说明（由工具执行端校验）：" + (fmt["definition"] if fmt["type"] == "grammar" else "自由文本")
                else:
                    params = object_at(tool.get("parameters", {}), location + ".parameters")
            previous = self.bindings.get(binding.alias)
            require(previous is None or previous == binding, location + ".name", "tool alias conflicts with another tool")
            function = {"name": binding.alias, "parameters": copy.deepcopy(params),
                        "description": f"{description}\n{binding.namespace or ''}/{binding.name}\n{desc}"}
            if "strict" in tool:
                require(isinstance(tool["strict"], bool), location + ".strict", "must be boolean")
                function["strict"] = tool["strict"]
            self.bindings[binding.alias] = binding
            self.tools[binding.alias] = {"type": "function", "function": function}

    def assistant(self):
        if not self.messages or self.messages[-1]["role"] != "assistant":
            self.messages.append({"role": "assistant", "content": None})
        return self.messages[-1]

    def add_input(self, values):
        if isinstance(values, str):
            self.messages.append({"role": "user", "content": values})
            return
        for index, raw in enumerate(array_at(values, "input")):
            path = f"input[{index}]"
            item = object_at(raw, path)
            kind = item.get("type", "message")
            if kind == "message":
                role = item.get("role")
                require(role in ("system", "developer", "user", "assistant"), path + ".role", "is not supported")
                content = content_parts(item.get("content"), path + ".content")
                if role == "assistant" and self.messages and self.messages[-1]["role"] == "assistant" and self.messages[-1]["content"] is None:
                    self.messages[-1]["content"] = content
                else:
                    self.messages.append({"role": role, "content": content})
            elif kind == "reasoning":
                require(not item.get("encrypted_content"), path + ".encrypted_content", "native encrypted reasoning is not supported")
                texts = []
                for field, expected in (("content", "reasoning_text"), ("summary", "summary_text")):
                    for part in array_at(item.get(field, []), path + "." + field):
                        object_at(part, path + "." + field)
                        require(part.get("type") == expected, path + "." + field, "has unsupported content")
                        texts.append(string_at(part.get("text"), path + "." + field + ".text", empty=True))
                    if texts:
                        break
                if texts:
                    message = self.assistant()
                    message["reasoning_content"] = message.get("reasoning_content", "") + "".join(texts)
            elif kind in ("function_call", "custom_tool_call", "tool_search_call"):
                call_id = string_at(item.get("call_id"), path + ".call_id")
                require(call_id not in self.calls, path + ".call_id", "is duplicated")
                if kind == "tool_search_call":
                    require(item.get("execution") == "client", path + ".execution", "must be client")
                    binding = ToolBinding("tool_search", "cb2a_tool_search")
                    arguments = json.dumps(object_at(item.get("arguments"), path + ".arguments"), ensure_ascii=False)
                else:
                    binding = binding_for(item, path, "custom" if kind == "custom_tool_call" else "function")
                    if binding.kind == "custom":
                        arguments = json.dumps({"input": string_at(item.get("input"), path + ".input", empty=True)}, ensure_ascii=False)
                    else:
                        arguments = string_at(item.get("arguments"), path + ".arguments", empty=True)
                self.calls[call_id] = kind
                self.assistant().setdefault("tool_calls", []).append({"id": call_id, "type": "function", "function": {"name": binding.alias, "arguments": arguments}})
            else:
                expected = {"function_call_output": "function_call", "custom_tool_call_output": "custom_tool_call", "tool_search_output": "tool_search_call"}
                require(isinstance(kind, str) and kind in expected, path + ".type", "is not supported")
                call_id = string_at(item.get("call_id"), path + ".call_id")
                require(self.calls.get(call_id) == expected[kind] and call_id not in self.results, path + ".call_id", "must reference an unmatched call of the same type")
                self.results.add(call_id)
                if kind == "tool_search_output":
                    require(item.get("execution", "client") == "client", path + ".execution", "must be client")
                    self.add_tools(item.get("tools"), path + ".tools")
                    content = json.dumps(list(self.tools.values()), ensure_ascii=False)
                else:
                    content = content_parts(item.get("output"), path + ".output")
                self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})

    def choose(self, choice):
        if isinstance(choice, str):
            require(choice in ("auto", "none", "required"), "tool_choice", "is not supported")
            return choice
        object_at(choice, "tool_choice")
        if choice.get("type") == "allowed_tools":
            selected = array_at(choice.get("tools"), "tool_choice.tools")
            mode = choice.get("mode", "auto")
            require(mode in ("auto", "required"), "tool_choice.mode", "is not supported")
        else:
            selected, mode = [choice], "required"
        aliases = set()
        for item in selected:
            object_at(item, "tool_choice.tools")
            binding = binding_for(item, "tool_choice", item.get("type"))
            require(self.bindings.get(binding.alias) == binding, "tool_choice", "references an unavailable tool")
            aliases.add(binding.alias)
        require(bool(aliases), "tool_choice", "must select at least one tool")
        self.tools = {key: value for key, value in self.tools.items() if key in aliases}
        self.bindings = {key: value for key, value in self.bindings.items() if key in aliases}
        return mode


def translate_responses_request(body):
    """只复制明确映射的字段，不将客户端身份或缓存元数据交给上游。"""
    object_at(body, "body")
    model = string_at(body.get("model"), "model")
    for field in ("store", "background"):
        value = body.get(field)
        require(value is None or value is False, field, "only false is supported")
    for field in ("previous_response_id", "conversation", "context_management", "prompt", "truncation"):
        allowed = (None, "disabled") if field == "truncation" else (None,)
        require(body.get(field) in allowed, field, "is not supported by the stateless gateway")
    translator = _Translator()
    instructions = body.get("instructions")
    if instructions is not None:
        translator.messages.append({"role": "system", "content": string_at(instructions, "instructions", empty=True)})
    translator.add_input(body.get("input", []))
    require(bool(translator.messages), "input", "must provide messages or instructions")
    translator.add_tools(body.get("tools", []))
    payload = {"model": model, "messages": translator.messages, "stream": body.get("stream", False)}
    if body.get("max_output_tokens") is not None:
        payload["max_tokens"] = body["max_output_tokens"]
    validate_options(payload)
    for field in ("temperature", "top_p"):
        value = body.get(field)
        if value is not None:
            require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), field, "must be a finite number")
            payload[field] = value
    if body.get("parallel_tool_calls") is not None:
        require(isinstance(body["parallel_tool_calls"], bool), "parallel_tool_calls", "must be boolean")
        payload["parallel_tool_calls"] = body["parallel_tool_calls"]
    if body.get("reasoning") is not None:
        reasoning = object_at(body["reasoning"], "reasoning")
        effort = reasoning.get("effort")
        if effort is not None:
            require(effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"), "reasoning.effort", "is not supported")
            payload["reasoning_effort"] = effort
            if effort == "none":
                payload.update(thinking={"type": "disabled"}, enable_thinking=False)
    if body.get("text") is not None:
        text = object_at(body["text"], "text")
        fmt = text.get("format")
        if fmt is not None:
            object_at(fmt, "text.format")
            kind = fmt.get("type")
            require(kind in ("text", "json_object", "json_schema"), "text.format.type", "is not supported")
            if kind == "json_schema":
                schema = {"name": string_at(fmt.get("name"), "text.format.name"), "schema": copy.deepcopy(object_at(fmt.get("schema"), "text.format.schema"))}
                for field in ("description", "strict"):
                    if field in fmt:
                        require(isinstance(fmt[field], str if field == "description" else bool), "text.format." + field, "has invalid type")
                        schema[field] = fmt[field]
                payload["response_format"] = {"type": kind, "json_schema": schema}
            else:
                payload["response_format"] = {"type": kind}
        if text.get("verbosity") is not None:
            require(text["verbosity"] in ("low", "medium", "high"), "text.verbosity", "is not supported")
            payload["verbosity"] = text["verbosity"]
    choice = body.get("tool_choice")
    payload["tool_choice"] = translator.choose("auto" if choice is None else choice)
    if translator.tools:
        payload["tools"] = list(translator.tools.values())
    return payload, translator.bindings
