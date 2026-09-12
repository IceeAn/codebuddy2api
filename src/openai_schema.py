"""OpenAI 请求文档：明确常用字段并允许未来扩展。"""

CONTENT_PART_SCHEMA = {
    "type": "object",
    "required": ["type"],
    "additionalProperties": True,
    "description": "已知类型校验结构；其他类型原样透传，能力由上游决定。",
    "properties": {
        "type": {"type": "string", "examples": ["text", "image_url", "input_audio", "file", "refusal"]},
        "text": {"type": "string"},
        "refusal": {"type": "string"},
        "image_url": {
            "type": "object", "required": ["url"], "additionalProperties": True,
            "properties": {
                "url": {"type": "string", "description": "图片 URL 或 Base64 data URL；网关不下载或转码。"},
                "detail": {"type": "string", "enum": ["auto", "low", "high"]},
            },
        },
        "input_audio": {
            "type": "object", "required": ["data", "format"], "additionalProperties": True,
            "description": "仅透传；当前实测 CodeBuddy 拒绝此类型。",
            "properties": {"data": {"type": "string"}, "format": {"type": "string"}},
        },
        "file": {
            "type": "object", "additionalProperties": True,
            "anyOf": [{"required": ["file_data"]}, {"required": ["file_id"]}],
            "description": "仅透传；网关不提供 Files API，当前实测 CodeBuddy 拒绝此类型。",
            "properties": {"file_data": {"type": "string"}, "file_id": {"type": "string"},
                           "filename": {"type": "string"}},
        },
    },
}

CONTENT_SCHEMA = {"anyOf": [
    {"type": "string"}, {"type": "null"},
    {"type": "array", "items": CONTENT_PART_SCHEMA},
]}

TOKEN_LIMIT_SCHEMA = {
    "anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}],
    "description": "新旧 token 上限只能指定同一个值；实际截断和计数由 CodeBuddy 决定。",
}

EXTRA_REQUEST_PROPERTIES = {
    "max_completion_tokens": {**TOKEN_LIMIT_SCHEMA, "description": "映射为上游 max_tokens；与 max_tokens 不同值时返回 400。"},
    "max_tokens": TOKEN_LIMIT_SCHEMA,
    "stream_options": {"anyOf": [{"type": "null"}, {
        "type": "object", "additionalProperties": True,
        "properties": {"include_usage": {"type": "boolean", "default": False}},
    }]},
    "response_format": {"type": "object", "additionalProperties": True,
                        "description": "原样透传；当前实测不保证 JSON mode 或严格 schema。"},
    "modalities": {"type": "array", "items": {"type": "string"}, "description": "原样透传，不保证音频生成。"},
    "audio": {"type": "object", "additionalProperties": True},
    "logprobs": {"type": "boolean"},
    "top_logprobs": {"type": "integer"},
    "parallel_tool_calls": {"type": "boolean"},
    "n": {"type": "integer", "description": "仅单 choice 受保证；n > 1 保留既有行为，可能丢失或重复候选。"},
}
