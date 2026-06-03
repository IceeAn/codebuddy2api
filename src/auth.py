"""
Authentication module for CodeBuddy2API
"""
import base64
import binascii
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from config import get_users_file_path
from .password_hashing import verify_password

logger = logging.getLogger(__name__)
router = APIRouter()

# 用于不存在用户名时的固定耗时校验，避免通过响应时间枚举有效用户名。
_DUMMY_PASSWORD_HASH = "pbkdf2_sha256$390000$Q2ZpaYeWHUv958nZM_Zl6A$Z7iHCysOlsWDVbFAIt2uxSfhQTD5qYNehS1W65K4DHY"
SESSION_COOKIE_NAME = "codebuddy2api_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_sessions: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class AuthenticatedUser:
    """当前通过认证的用户。"""
    username: str
    source: str


class LoginRequest(BaseModel):
    """管理页登录请求。"""
    username: str
    password: str


class UsersFileStore:
    """从 secrets/users.txt 加载用户名和密码哈希。"""

    def __init__(self):
        self._users: Dict[str, str] = {}
        self._loaded_path: Optional[Path] = None
        self._loaded_mtime: Optional[float] = None

    def _resolve_users_file(self) -> Path:
        users_file = Path(get_users_file_path())
        if not users_file.is_absolute():
            users_file = Path.cwd() / users_file
        return users_file

    def _load_if_needed(self):
        users_file = self._resolve_users_file()
        try:
            stat_result = users_file.stat()
        except FileNotFoundError:
            self._users = {}
            self._loaded_path = users_file
            self._loaded_mtime = None
            return

        if not users_file.is_file():
            self._users = {}
            self._loaded_path = users_file
            self._loaded_mtime = None
            logger.warning("Configured users file is not a regular file: %s", users_file)
            return

        current_mtime = stat_result.st_mtime

        if self._loaded_path == users_file and self._loaded_mtime == current_mtime:
            return

        users: Dict[str, str] = {}
        with users_file.open("r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                username, separator, password_hash = stripped.partition(":")
                if not separator or not username.strip() or not password_hash.strip():
                    logger.warning("Ignoring invalid users file line %s in %s", line_number, users_file)
                    continue

                users[username.strip()] = password_hash.strip()

        self._users = users
        self._loaded_path = users_file
        self._loaded_mtime = current_mtime
        logger.info("Loaded %s user(s) from %s", len(users), users_file)

    def verify(self, username: str, password: str) -> bool:
        self._load_if_needed()
        password_hash = self._users.get(username)
        if not password_hash:
            verify_password(password, _DUMMY_PASSWORD_HASH)
            return False
        return verify_password(password, password_hash)

    def has_users_file(self) -> bool:
        self._load_if_needed()
        return bool(self._users)

    def has_username(self, username: str) -> bool:
        self._load_if_needed()
        return username in self._users


def _parse_basic_credentials(auth_value: str) -> Optional[Tuple[str, str]]:
    try:
        encoded = auth_value.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, separator, password = decoded.partition(":")
        if not separator:
            return None
        return username, password
    except (IndexError, UnicodeDecodeError, binascii.Error):
        return None


def _parse_bearer_credentials(auth_value: str) -> Tuple[Optional[str], str]:
    token = auth_value.split(" ", 1)[1].strip()
    username, separator, password = token.partition(":")
    if separator:
        return username, password
    return None, token


_users_store = UsersFileStore()


def _cleanup_expired_sessions() -> None:
    now = time.time()
    expired_session_ids = [
        session_id
        for session_id, session_data in _sessions.items()
        if session_data.get("expires_at", 0) <= now
    ]
    for session_id in expired_session_ids:
        _sessions.pop(session_id, None)


def _create_session(username: str) -> str:
    _cleanup_expired_sessions()
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "username": username,
        "expires_at": time.time() + SESSION_TTL_SECONDS,
    }
    return session_id


def _get_session_user(session_id: Optional[str]) -> Optional[AuthenticatedUser]:
    if not session_id:
        return None

    _cleanup_expired_sessions()
    session_data = _sessions.get(session_id)
    if not session_data:
        return None

    username = str(session_data.get("username") or "")
    if not username or not _users_store.has_username(username):
        _sessions.pop(session_id, None)
        return None

    session_data["expires_at"] = time.time() + SESSION_TTL_SECONDS
    return AuthenticatedUser(username=username, source="session_cookie")


def _invalidate_session(session_id: Optional[str]) -> None:
    if session_id:
        _sessions.pop(session_id, None)


def authenticate(request: Request) -> AuthenticatedUser:
    """验证用户身份，支持 Basic、Bearer username:password 和管理页会话 cookie。"""
    auth_value = request.headers.get("Authorization", "")
    scheme = auth_value.split(" ", 1)[0].lower() if auth_value else ""

    if not _users_store.has_users_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No authentication users configured. Mount secrets/users.txt.",
        )

    if scheme == "basic":
        parsed = _parse_basic_credentials(auth_value)
        if not parsed:
            raise _auth_error()
        username, password = parsed
        if _users_store.verify(username, password):
            return AuthenticatedUser(username=username, source="users_file")
        raise _auth_error()

    if scheme == "bearer":
        username, password = _parse_bearer_credentials(auth_value)
        if username and _users_store.verify(username, password):
            return AuthenticatedUser(username=username, source="users_file")

        raise _auth_error()

    session_user = _get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
    if session_user:
        return session_user

    raise _auth_error()


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid username or password",
        headers={"WWW-Authenticate": "Basic"},
    )


def _is_secure_request(request: Request) -> bool:
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


@router.post("/auth/login")
async def login(request: Request, response: Response, credentials: LoginRequest):
    """登录管理页并写入 HttpOnly 会话 cookie。"""
    if not _users_store.has_users_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No authentication users configured. Mount secrets/users.txt.",
        )

    username = credentials.username.strip()
    if not username or not credentials.password:
        raise _auth_error()

    if not _users_store.verify(username, credentials.password):
        raise _auth_error()

    session_id = _create_session(username)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
        path="/",
    )
    return {"authenticated": True, "username": username}


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    """退出管理页登录并清理会话 cookie。"""
    _invalidate_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"authenticated": False}


@router.get("/auth/session")
async def get_session(_user: AuthenticatedUser = Depends(authenticate)):
    """返回当前管理页会话状态。"""
    return {
        "authenticated": True,
        "username": _user.username,
        "source": _user.source,
    }
