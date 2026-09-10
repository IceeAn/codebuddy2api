"""认证相关类型和常量。"""
from dataclasses import dataclass
from typing import Annotated, Optional

from pydantic import BaseModel, StringConstraints

DUMMY_PASSWORD_HASH = "pbkdf2_sha256$600000$Q2ZpaYeWHUv958nZM_Zl6A$cRI0uf1Yms6VBjzrG-XchKqQzqc6GSC1w09w2070AH8"
SESSION_COOKIE_NAME = "codebuddy2api_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
SESSION_REFRESH_STATE_KEY = "session_cookie_refresh"
SESSION_SUPPRESS_REFRESH_STATE_KEY = "session_cookie_suppress_refresh"
SESSION_DELETE_STATE_KEY = "session_cookie_delete"
API_KEY_PREFIX = "sk-"


@dataclass(frozen=True)
class AuthenticatedUser:
    """当前通过认证的用户。"""

    username: str
    source: str
    api_key_id: Optional[str] = None
    api_key_name: Optional[str] = None
    password_change_required: bool = False
    auth_revision: Optional[bytes] = None


class LoginRequest(BaseModel):
    """管理页登录请求。"""

    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    """当前账号修改自身密码请求。未知字段按兼容约定忽略。"""

    new_password: str
    current_password: Optional[str] = None


class ApiKeyCreateRequest(BaseModel):
    """API Key 创建请求。"""

    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
