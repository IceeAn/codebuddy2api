"""CodeBuddy 中立事件到 Responses 生命周期和输出项目的编码。"""
import copy
import json
import time
import uuid

from .codebuddy_events import CodeBuddyResponseEvent, ToolCallIndexState, UpstreamProtocolViolation


def ensure(condition, message):
    if not condition:
        raise UpstreamProtocolViolation(message)


def reject_constant(_value):
    raise ValueError("Non-standard JSON constant")


def arguments_object(value):
    try:
        parsed = json.loads(value, parse_constant=reject_constant)
    except (ValueError, TypeError) as error:
        raise UpstreamProtocolViolation("Invalid upstream tool arguments") from error
    ensure(isinstance(parsed, dict), "Upstream tool arguments must be an object")
    return parsed


def response_usage(value):
    if value is None:
        return None
    ensure(isinstance(value, dict), "Invalid upstream usage")
    result = {}
    for source, target in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
        number = value.get(source)
        ensure(isinstance(number, int) and not isinstance(number, bool) and number >= 0, "Invalid upstream token count")
        result[target] = number
    total = value.get("total_tokens", result["input_tokens"] + result["output_tokens"])
    ensure(isinstance(total, int) and not isinstance(total, bool) and total >= 0, "Invalid upstream total tokens")
    result["total_tokens"] = total
    for source, target, fields in (("prompt_tokens_details", "input_tokens_details", ("cached_tokens", "cache_write_tokens")),
                                   ("completion_tokens_details", "output_tokens_details", ("reasoning_tokens",))):
        details = value.get(source)
        if details is not None:
            ensure(isinstance(details, dict), "Invalid upstream token details")
            mapped = {}
            for field in fields:
                if field in details:
                    number = details[field]
                    ensure(isinstance(number, int) and not isinstance(number, bool) and number >= 0, "Invalid upstream token details")
                    mapped[field] = number
            if mapped:
                result[target] = mapped
    return result


class _State:
    def __init__(self, adapter):
        self.adapter = adapter
        self.sequence = 0
        self.started = False
        self.closed = False
        self.output = []
        self.scalar = None
        self.pending = {}
        self.indexes = ToolCallIndexState()
        self.call_ids = set()
        self.finish = None
        self.usage = None

    def envelope(self, status="in_progress", error=None):
        incomplete = {"length": "max_output_tokens", "content_filter": "content_filter"}.get(self.finish)
        return {"id": self.adapter.response_id, "object": "response", "created_at": self.adapter.created_at,
                "model": self.adapter.model, "status": status, "output": copy.deepcopy(self.output),
                "error": error, "incomplete_details": {"reason": incomplete} if status == "incomplete" else None,
                "usage": copy.deepcopy(self.usage), "store": False, "parallel_tool_calls": self.adapter.parallel_tool_calls,
                "tools": copy.deepcopy(self.adapter.tools), "tool_choice": copy.deepcopy(self.adapter.tool_choice)}

    def emit(self, kind, **fields):
        value = {"type": kind, "sequence_number": self.sequence, **fields}
        self.sequence += 1
        return f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n\n"

    def start(self):
        if self.started:
            return []
        self.started = True
        return [self.emit("response.created", response=self.envelope()),
                self.emit("response.in_progress", response=self.envelope())]

    def add_item(self, item):
        index = len(self.output)
        item["id"] = f"{self.adapter.response_id}_item_{index}"
        self.output.append(item)
        return index, self.emit("response.output_item.added", output_index=index, item=copy.deepcopy(item))

    def close_scalar(self, status="completed"):
        if self.scalar is None:
            return []
        index, kind = self.scalar
        item = self.output[index]
        events = []
        if kind == "reasoning":
            part = item["summary"][0]
            events.append(self.emit("response.reasoning_summary_text.done", item_id=item["id"], output_index=index, summary_index=0, text=part["text"]))
            events.append(self.emit("response.reasoning_summary_part.done", item_id=item["id"], output_index=index, summary_index=0, part=part))
        else:
            part = item["content"][0]
            field = "refusal" if kind == "refusal" else "text"
            details = {"logprobs": []} if kind == "output_text" else {}
            events.append(self.emit(f"response.{kind}.done", item_id=item["id"], output_index=index, content_index=0,
                                    **{field: part[field]}, **details))
            events.append(self.emit("response.content_part.done", item_id=item["id"], output_index=index, content_index=0, part=part))
        item["status"] = status
        events.append(self.emit("response.output_item.done", output_index=index, item=item))
        self.scalar = None
        return events

    def text(self, kind, value):
        ensure(isinstance(value, str), "Unsupported upstream content")
        if not value:
            return []
        events = self.flush_tools()
        if self.scalar is None or self.scalar[1] != kind:
            events += self.close_scalar()
            if kind == "reasoning":
                item = {"type": "reasoning", "summary": [], "status": "in_progress"}
                index, added = self.add_item(item)
                part = {"type": "summary_text", "text": ""}
                item["summary"].append(part)
                events += [added, self.emit("response.reasoning_summary_part.added", item_id=item["id"], output_index=index, summary_index=0, part=part)]
            else:
                item = {"type": "message", "role": "assistant", "status": "in_progress", "content": []}
                index, added = self.add_item(item)
                part = {"type": "refusal", "refusal": ""} if kind == "refusal" else {"type": "output_text", "text": "", "annotations": []}
                item["content"].append(part)
                events += [added, self.emit("response.content_part.added", item_id=item["id"], output_index=index, content_index=0, part=part)]
            self.scalar = (index, kind)
        index, _ = self.scalar
        item = self.output[index]
        if kind == "reasoning":
            item["summary"][0]["text"] += value
            events.append(self.emit("response.reasoning_summary_text.delta", item_id=item["id"], output_index=index, summary_index=0, delta=value))
        else:
            item["content"][0]["refusal" if kind == "refusal" else "text"] += value
            # 未提供概率条目时输出空列表，满足严格客户端的文本事件结构。
            details = {"logprobs": []} if kind == "output_text" else {}
            events.append(self.emit(f"response.{kind}.delta", item_id=item["id"], output_index=index, content_index=0,
                                    delta=value, **details))
        return events

    def tools(self, calls):
        ensure(isinstance(calls, list), "Invalid upstream tool calls")
        events = self.close_scalar() if calls else []
        for call in calls:
            ensure(isinstance(call, dict), "Invalid upstream tool call")
            ensure(call.get("type") in (None, "function"), "Unsupported upstream tool type")
            explicit = call.get("index")
            ensure(explicit is None or (isinstance(explicit, int) and not isinstance(explicit, bool) and explicit >= 0), "Invalid upstream tool index")
            index = self.indexes.resolve(call)
            ensure(index is not None, "Missing upstream tool index and ID")
            current = self.pending.setdefault(index, {"id": None, "name": None, "arguments": ""})
            if call.get("id") not in (None, ""):
                identifier = call["id"]
                ensure(isinstance(identifier, str), "Invalid upstream call ID")
                ensure(current["id"] in (None, identifier), "Upstream call ID changed")
                current["id"] = identifier
            function = call.get("function")
            if function is not None:
                ensure(isinstance(function, dict), "Invalid upstream function")
                name = function.get("name")
                # CodeBuddy 在后续参数分块中可能重复发送空名称，不能覆盖首块元数据。
                if name not in (None, ""):
                    ensure(isinstance(name, str), "Invalid upstream function name")
                    ensure(current["name"] in (None, name), "Upstream function name changed")
                    current["name"] = name
                arguments = function.get("arguments")
                if arguments is None:
                    arguments = ""
                ensure(isinstance(arguments, str), "Invalid upstream arguments fragment")
                current["arguments"] += arguments
        return events

    def flush_tools(self):
        events = []
        for index in sorted(self.pending):
            call = self.pending[index]
            binding = self.adapter.bindings.get(call["name"])
            ensure(binding is not None, "Upstream called an undeclared tool")
            ensure(call["id"] and call["id"] not in self.call_ids, "Missing or duplicate upstream call ID")
            args = arguments_object(call["arguments"])
            self.call_ids.add(call["id"])
            item = {"type": "function_call", "name": binding.name, "call_id": call["id"], "status": "in_progress", "arguments": ""}
            if binding.namespace is not None:
                item["namespace"] = binding.namespace
            if binding.kind == "tool_search":
                item = {"type": "tool_search_call", "call_id": call["id"], "status": "in_progress", "execution": "client", "arguments": args}
                output_index, added = self.add_item(item)
                events.append(added)
            else:
                if binding.kind == "custom":
                    ensure(set(args) == {"input"} and isinstance(args["input"], str), "Custom tool arguments must contain only an input string")
                    item.pop("arguments")
                    item.update(type="custom_tool_call", input="")
                    field, prefix, value = "input", "custom_tool_call_input", args["input"]
                else:
                    field, prefix, value = "arguments", "function_call_arguments", call["arguments"]
                output_index, added = self.add_item(item)
                events += [added, self.emit(f"response.{prefix}.delta", item_id=item["id"], output_index=output_index, delta=value)]
                item[field] = value
                events.append(self.emit(f"response.{prefix}.done", item_id=item["id"], output_index=output_index, **{field: value}))
            item["status"] = "completed"
            events.append(self.emit("response.output_item.done", output_index=output_index, item=item))
        self.pending.clear()
        # 上游工具 index 在下一连续工具组可能从零开始。
        self.indexes = ToolCallIndexState()
        return events

    def process(self, event):
        if not isinstance(event, CodeBuddyResponseEvent):
            return []
        ensure(not self.closed, "Events after response completion")
        events = self.start()
        ensure(not event.delta.get("audio"), "Unsupported upstream response content")
        legacy = event.delta.get("function_call")
        # 工具流结尾可能携带空的旧版占位对象，不代表发生了旧版调用。
        if legacy:
            ensure(isinstance(legacy, dict) and all(value in (None, "", [], {}) for value in legacy.values()),
                   "Unsupported upstream response content")
        for key, kind in (("reasoning_content", "reasoning"), ("content", "output_text"), ("refusal", "refusal")):
            value = event.delta.get(key)
            if value is not None:
                events += self.text(kind, value)
        if event.raw_tool_calls is not None:
            events += self.tools(event.raw_tool_calls)
        if event.finish_reason is not None:
            ensure(event.finish_reason in ("stop", "tool_calls", "length", "content_filter"), "Unsupported upstream finish reason")
            self.finish = event.finish_reason
        if event.usage is not None:
            self.usage = response_usage(event.usage)
        return events

    def finalize(self):
        ensure(not self.closed and self.finish is not None, "Upstream response ended without a finish reason")
        status = "incomplete" if self.finish in ("length", "content_filter") else "completed"
        events = self.start() + self.close_scalar(status)
        if status == "incomplete":
            # 截断的连续工具组可能包含半截 JSON，不校验或发布为可执行调用。
            self.pending.clear()
        else:
            events += self.flush_tools()
        self.closed = True
        return events + [self.emit("response." + status, response=self.envelope(status))]


class ResponsesAdapter:
    """共享传输层的 Responses 适配器；每个请求独立持有事件序号。"""
    media_type = "text/event-stream"
    stream_headers = {"X-Accel-Buffering": "no", "Cache-Control": "private, no-store"}

    def __init__(self, model, bindings, *, response_id=None, created_at=None, parallel_tool_calls=True,
                 tools=None, tool_choice="auto"):
        self.model = model
        self.bindings = bindings
        self.parallel_tool_calls = parallel_tool_calls
        self.tools = copy.deepcopy([] if tools is None else tools)
        self.tool_choice = copy.deepcopy(tool_choice)
        self.response_id = response_id or "resp_" + uuid.uuid4().hex
        self.created_at = int(time.time()) if created_at is None else created_at
        self.state = None

    def create_stream_state(self):
        self.state = _State(self)
        return self.state

    @staticmethod
    def process_stream_event(state, event):
        return state.process(event)

    @staticmethod
    def finalize_stream(state, _upstream_done):
        return state.finalize()

    def format_stream_error(self, error):
        state = self.state
        value = {"code": "upstream_error", "message": "CodeBuddy response failed"}
        # 只使用受控错误说明，不能回显工具参数或内部异常。
        if getattr(error, "status_code", None) == 504:
            value = {"code": "server_error", "message": "CodeBuddy response timed out"}
        return state.emit("response.failed", response=state.envelope("failed", value))

    def create_non_stream_aggregator(self):
        return _State(self)

    @staticmethod
    def process_non_stream_event(state, event):
        state.process(event)

    @staticmethod
    def finalize_non_stream(state, _upstream_done):
        state.finalize()
        return state.envelope("incomplete" if state.finish in ("length", "content_filter") else "completed")
