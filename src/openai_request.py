"""校验网关明确处理的字段，保留未知上游扩展。"""
from typing import Any

from .openai_errors import OpenAIRequestError


def require(condition: bool, path: str, expectation: str) -> None:
    if not condition:
        raise OpenAIRequestError(f"{path} {expectation}", path)


def require_string(value: Any, path: str, *, allow_empty: bool = False) -> None:
    require(isinstance(value, str) and (allow_empty or bool(value)), path, "must be a string")


def validate_content(content: Any, path: str) -> None:
    if content is None or isinstance(content, str):
        return
    require(isinstance(content, list), path, "must be a string, array or null")
    for index, part in enumerate(content):
        part_path = f"{path}[{index}]"
        require(isinstance(part, dict), part_path, "must be an object")
        kind = part.get("type")
        require_string(kind, f"{part_path}.type")
        if kind in {"text", "refusal"}:
            require_string(part.get(kind), f"{part_path}.{kind}", allow_empty=True)
        elif kind in {"image_url", "input_audio", "file"}:
            data = part.get(kind)
            nested = f"{part_path}.{kind}"
            require(isinstance(data, dict), nested, "must be an object")
            if kind == "image_url":
                require_string(data.get("url"), nested + ".url")
                require(data.get("detail", "auto") in ("auto", "low", "high"),
                        nested + ".detail", "must be auto, low or high")
            elif kind == "input_audio":
                require_string(data.get("data"), nested + ".data")
                require_string(data.get("format"), nested + ".format")
            else:
                require(bool(data.get("file_data") or data.get("file_id")), nested,
                        "must contain file_data or file_id")
                for name in ("file_data", "file_id", "filename"):
                    if name in data:
                        require_string(data[name], nested + "." + name)


def validate_options(body: dict) -> None:
    for name in ("max_tokens", "max_completion_tokens"):
        value = body.get(name)
        require(value is None or (isinstance(value, int) and not isinstance(value, bool) and value > 0),
                name, "must be a positive integer or null")
    old, new = body.get("max_tokens"), body.get("max_completion_tokens")
    require(old is None or new is None or old == new, "max_completion_tokens", "conflicts with max_tokens")
    require(body.get("stream") is None or isinstance(body["stream"], bool), "stream", "must be boolean or null")
    options = body.get("stream_options")
    require(options is None or isinstance(options, dict), "stream_options", "must be an object or null")
    if isinstance(options, dict) and "include_usage" in options:
        require(isinstance(options["include_usage"], bool), "stream_options.include_usage", "must be boolean")


def map_token_limit(payload: dict) -> None:
    """入口已完成校验；共享准备流程不重复施加 OpenAI 专属校验。"""
    new = payload.pop("max_completion_tokens", None)
    if new is not None:
        payload["max_tokens"] = new
    if payload.get("max_tokens") is None:
        payload.pop("max_tokens", None)
