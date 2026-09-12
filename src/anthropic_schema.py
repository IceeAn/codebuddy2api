"""Anthropic 请求可转换内容的 OpenAPI 定义。"""


def _block(kind, properties, required):
    return {"type": "object", "additionalProperties": True,
            "required": ["type", *required],
            "properties": {"type": {"const": kind}, **properties}}


STRING = {"type": "string", "minLength": 1}
TEXT_BLOCKS = [_block(kind, {"text": STRING}, ["text"]) for kind in ("text", "input_text")]
IMAGE_BLOCK = _block("image", {"source": {"oneOf": [
    _block("base64", {"media_type": {"enum": ["image/png", "image/jpeg", "image/gif", "image/webp"]}, "data": STRING}, ["media_type", "data"]),
    _block("url", {"url": STRING}, ["url"]),
]}}, ["source"])
DOCUMENT_BLOCK = _block("document", {
    "source": {"oneOf": [
        _block("text", {"media_type": {"const": "text/plain"}, "data": STRING}, ["media_type", "data"]),
        _block("content", {"content": {"anyOf": [STRING, {"type": "array", "minItems": 1, "items": {"oneOf": [*TEXT_BLOCKS, IMAGE_BLOCK]}}]}}, ["content"]),
    ]},
    "title": {"type": ["string", "null"]}, "context": {"type": ["string", "null"]},
    "citations": {"type": ["object", "null"], "properties": {"enabled": {"const": False}}},
}, ["source"])
INPUT_BLOCKS = [*TEXT_BLOCKS, IMAGE_BLOCK, DOCUMENT_BLOCK]
TOOL_RESULT_BLOCK = _block("tool_result", {
    "tool_use_id": STRING, "is_error": {"type": "boolean"},
    "content": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"oneOf": INPUT_BLOCKS}}]},
}, ["tool_use_id"])
MESSAGE_CONTENT = {"description": "图片和文档仅支持 user 消息及其 tool_result；system 仅支持文本，assistant 支持文本、thinking 和 tool_use。",
                   "anyOf": [STRING, {"type": "array", "minItems": 1, "items": {"oneOf": [
                       *INPUT_BLOCKS, TOOL_RESULT_BLOCK,
                       _block("tool_use", {"id": STRING, "name": STRING, "input": {"type": "object"}}, ["id", "name", "input"]),
                       _block("thinking", {"thinking": STRING, "signature": STRING}, ["thinking", "signature"]),
                   ]}}]}
OUTPUT_CONFIG = {
    "type": ["object", "null"], "additionalProperties": True,
    "description": "仅映射已知字段；上游不保证执行 JSON Schema 或思考强度约束。",
    "properties": {
        "effort": {"enum": ["low", "medium", "high", "xhigh", "max", None]},
        "format": {"type": ["object", "null"], "required": ["type", "schema"],
                   "properties": {"type": {"const": "json_schema"}, "schema": {"type": "object"}}},
    },
}
TOOLS = {"type": "array", "items": {"type": "object", "required": ["name", "input_schema"],
          "properties": {"name": STRING, "input_schema": {"type": "object"},
                         "strict": {"type": ["boolean", "null"], "description": "映射到上游 function.strict，不保证约束生效。"}}}}
