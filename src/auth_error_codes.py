"""账号与密码业务错误码的唯一登记处。"""

from enum import Enum
from typing import Dict, Optional

from fastapi import HTTPException
from starlette.responses import JSONResponse


class AuthErrorCode(str, Enum):
    """前后端可稳定分支处理的账号业务错误。"""

    PASSWORD_CHANGE_REQUIRED = "password_change_required"
    CURRENT_PASSWORD_REQUIRED = "current_password_required"
    CURRENT_PASSWORD_NOT_ALLOWED = "current_password_not_allowed"
    CURRENT_PASSWORD_INCORRECT = "current_password_incorrect"
    NEW_PASSWORD_INVALID = "new_password_invalid"
    NEW_PASSWORD_UNCHANGED = "new_password_unchanged"
    PASSWORD_CHANGED_ELSEWHERE = "password_changed_elsewhere"
    PASSWORD_CHANGE_RATE_LIMITED = "password_change_rate_limited"
    BOOTSTRAP_EXPIRED = "bootstrap_expired"


AUTH_ERROR_DETAILS: Dict[AuthErrorCode, str] = {
    AuthErrorCode.PASSWORD_CHANGE_REQUIRED: "首次登录后必须先修改密码",
    AuthErrorCode.CURRENT_PASSWORD_REQUIRED: "请输入当前密码",
    AuthErrorCode.CURRENT_PASSWORD_NOT_ALLOWED: "首次修改密码时不得提交当前密码",
    AuthErrorCode.CURRENT_PASSWORD_INCORRECT: "当前密码错误",
    AuthErrorCode.NEW_PASSWORD_INVALID: "新密码必须为 8 至 128 个字符，且不能包含控制字符",
    AuthErrorCode.NEW_PASSWORD_UNCHANGED: "新密码不能与当前密码相同",
    AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE: "密码已由其他会话修改，请使用新密码登录",
    AuthErrorCode.PASSWORD_CHANGE_RATE_LIMITED: "密码操作过于频繁，请稍后重试",
    AuthErrorCode.BOOTSTRAP_EXPIRED: "初始账号已过期，请重启服务后重试",
}


class AuthBusinessError(HTTPException):
    """由专用异常处理器输出顶层 error_code/detail 信封。"""

    def __init__(
        self,
        status_code: int,
        error_code: AuthErrorCode,
        *,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.error_code = error_code
        super().__init__(
            status_code=status_code,
            detail=AUTH_ERROR_DETAILS[error_code],
            headers=headers,
        )


def auth_business_error_response(error: AuthBusinessError) -> JSONResponse:
    """生成不嵌套在 FastAPI detail 下的稳定业务错误响应。"""
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error_code": error.error_code.value,
            "detail": str(error.detail),
        },
        headers=error.headers,
    )
