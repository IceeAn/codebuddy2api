"""OpenAI 命名空间的统一错误信封。"""
from typing import Any, Optional

from fastapi import HTTPException
from starlette.responses import JSONResponse


def is_openai_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/")
               for prefix in ("/openai", "/api/admin/playground/openai"))


class OpenAIRequestError(HTTPException):
    """只携带字段位置，不回显请求值。"""

    def __init__(self, message: str, param: str):
        super().__init__(status_code=400, detail=message)
        self.param = param


def openai_error_content(
        status: int, message: str, *, error_type: Optional[str] = None,
        param: Optional[str] = None, code: Any = None,
) -> dict:
    return {"error": {
        "message": message,
        "type": error_type or {
            401: "authentication_error", 403: "permission_error",
            404: "not_found_error", 429: "rate_limit_error",
        }.get(status, "server_error" if status >= 500 else "invalid_request_error"),
        "param": param,
        "code": code,
    }}


def openai_error_response(status: int, message: str, *, headers=None, **fields) -> JSONResponse:
    return JSONResponse(status_code=status, content=openai_error_content(status, message, **fields),
                        headers={**(headers or {}), "Cache-Control": "private, no-store"})
