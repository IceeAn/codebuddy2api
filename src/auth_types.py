"""认证相关类型和常量。"""
from dataclasses import dataclass

from pydantic import BaseModel

DUMMY_PASSWORD_HASH = "pbkdf2_sha256$390000$Q2ZpaYeWHUv958nZM_Zl6A$Z7iHCysOlsWDVbFAIt2uxSfhQTD5qYNehS1W65K4DHY"
SESSION_COOKIE_NAME = "codebuddy2api_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
API_KEY_PREFIX = "sk-"


@dataclass(frozen=True)
class AuthenticatedUser:
    """当前通过认证的用户。"""

    username: str
    source: str


class LoginRequest(BaseModel):
    """管理页登录请求。"""

    username: str
    password: str


class ApiKeyCreateRequest(BaseModel):
    """API Key 创建请求。"""

    name: str = ""
