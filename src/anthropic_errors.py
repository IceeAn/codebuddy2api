"""Anthropic 路由使用的请求 ID 与错误信封。"""

import uuid
from typing import Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


ANTHROPIC_REQUEST_ID_STATE = "anthropic_request_id"


def get_anthropic_request_id(request: Request) -> str:
    request_id = getattr(request.state, ANTHROPIC_REQUEST_ID_STATE, None)
    if not isinstance(request_id, str):
        request_id = f"req_{uuid.uuid4().hex}"
        setattr(request.state, ANTHROPIC_REQUEST_ID_STATE, request_id)
    return request_id


class AnthropicAPIError(HTTPException):
    """可由全局处理器稳定编码的 Anthropic API 错误。"""

    def __init__(
            self,
            status_code: int,
            error_type: str,
            message: str,
            request_id: str,
            *,
            headers: Optional[Dict[str, str]] = None,
    ) -> None:
        safe_headers = dict(headers or {})
        safe_headers["request-id"] = request_id
        super().__init__(status_code=status_code, detail=message, headers=safe_headers)
        self.error_type = error_type
        self.message = message
        self.request_id = request_id
        self.safe_headers = safe_headers


def anthropic_error_response(error: AnthropicAPIError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={
            "type": "error",
            "error": {"type": error.error_type, "message": error.message},
            "request_id": error.request_id,
        },
        headers=error.safe_headers,
    )
