"""
Authentication module for CodeBuddy2API
"""
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from config import get_codebuddy_creds_dir, get_users_file_path
from .password_hashing import create_password_hash, verify_password

logger = logging.getLogger(__name__)
router = APIRouter()

# 用于不存在用户名时的固定耗时校验，避免通过响应时间枚举有效用户名。
_DUMMY_PASSWORD_HASH = "pbkdf2_sha256$390000$Q2ZpaYeWHUv958nZM_Zl6A$Z7iHCysOlsWDVbFAIt2uxSfhQTD5qYNehS1W65K4DHY"
SESSION_COOKIE_NAME = "codebuddy2api_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
API_KEY_PREFIX = "sk-"
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


class ApiKeyCreateRequest(BaseModel):
    """API Key 创建请求。"""
    name: str = ""


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


_users_store = UsersFileStore()


class ApiKeyStore:
    """管理用户生成的 sk- API Key。"""

    def __init__(self):
        self._keys: List[Dict[str, Any]] = []
        self._loaded_path: Optional[Path] = None
        self._loaded_mtime: Optional[float] = None

    def _resolve_api_keys_file(self) -> Path:
        creds_dir = Path(get_codebuddy_creds_dir())
        if not creds_dir.is_absolute():
            creds_dir = Path.cwd() / creds_dir
        return creds_dir / "api_keys.json"

    def _load_if_needed(self) -> None:
        api_keys_file = self._resolve_api_keys_file()
        try:
            stat_result = api_keys_file.stat()
        except FileNotFoundError:
            self._keys = []
            self._loaded_path = api_keys_file
            self._loaded_mtime = None
            return

        if api_keys_file.is_symlink() or not api_keys_file.is_file():
            self._keys = []
            self._loaded_path = api_keys_file
            self._loaded_mtime = None
            logger.warning("Configured API keys file is not a regular file: %s", api_keys_file)
            return

        current_mtime = stat_result.st_mtime
        if self._loaded_path == api_keys_file and self._loaded_mtime == current_mtime:
            return

        try:
            with api_keys_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error("Failed to load API keys file %s: %s", api_keys_file, e)
            data = {}

        keys = data.get("keys", []) if isinstance(data, dict) else []
        self._keys = [key for key in keys if isinstance(key, dict)]
        self._loaded_path = api_keys_file
        self._loaded_mtime = current_mtime

    def _save(self) -> None:
        api_keys_file = self._resolve_api_keys_file()
        api_keys_file.parent.mkdir(parents=True, exist_ok=True)

        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(api_keys_file, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = None
                json.dump({"keys": self._keys}, f, indent=2, ensure_ascii=False)
            os.chmod(api_keys_file, 0o600)
            self._loaded_path = api_keys_file
            self._loaded_mtime = api_keys_file.stat().st_mtime
        except Exception:
            if fd is not None:
                os.close(fd)
            raise

    @staticmethod
    def _safe_key_name(name: str) -> str:
        return str(name or "").strip()[:80] or "API Key"

    @staticmethod
    def _preview(api_key: str) -> str:
        return f"{api_key[:10]}...{api_key[-4:]}"

    def create_key(self, username: str, name: str = "") -> Dict[str, Any]:
        self._load_if_needed()
        api_key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"
        created_at = int(time.time())
        key_record = {
            "id": f"key_{secrets.token_urlsafe(12)}",
            "username": username,
            "name": self._safe_key_name(name),
            "key_hash": create_password_hash(api_key),
            "preview": self._preview(api_key),
            "created_at": created_at,
            "last_used_at": None,
        }
        self._keys.append(key_record)
        self._save()
        return {
            "id": key_record["id"],
            "name": key_record["name"],
            "api_key": api_key,
            "preview": key_record["preview"],
            "created_at": created_at,
            "last_used_at": None,
        }

    def verify(self, api_key: str) -> Optional[AuthenticatedUser]:
        if not api_key.startswith(API_KEY_PREFIX):
            verify_password(api_key, _DUMMY_PASSWORD_HASH)
            return None

        self._load_if_needed()
        matched_key: Optional[Dict[str, Any]] = None
        for key_record in self._keys:
            key_hash = key_record.get("key_hash")
            if isinstance(key_hash, str) and verify_password(api_key, key_hash):
                matched_key = key_record
                break

        if not matched_key:
            verify_password(api_key, _DUMMY_PASSWORD_HASH)
            return None

        username = str(matched_key.get("username") or "")
        if not username or not _users_store.has_username(username):
            return None

        matched_key["last_used_at"] = int(time.time())
        self._save()
        return AuthenticatedUser(username=username, source="api_key")

    def list_keys(self, username: str) -> List[Dict[str, Any]]:
        self._load_if_needed()
        return [
            {
                "id": key_record.get("id"),
                "name": key_record.get("name"),
                "preview": key_record.get("preview"),
                "created_at": key_record.get("created_at"),
                "last_used_at": key_record.get("last_used_at"),
            }
            for key_record in self._keys
            if key_record.get("username") == username
        ]

    def delete_key(self, username: str, key_id: str) -> bool:
        self._load_if_needed()
        original_count = len(self._keys)
        self._keys = [
            key_record
            for key_record in self._keys
            if not (key_record.get("username") == username and key_record.get("id") == key_id)
        ]
        if len(self._keys) == original_count:
            return False
        self._save()
        return True


api_key_store = ApiKeyStore()


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
    """验证用户身份，支持 Bearer sk- API Key 和管理页会话 cookie。"""
    auth_value = request.headers.get("Authorization", "")
    scheme = auth_value.split(" ", 1)[0].lower() if auth_value else ""

    if not _users_store.has_users_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No authentication users configured. Mount secrets/users.txt.",
        )

    if scheme == "bearer":
        api_key = auth_value.split(" ", 1)[1].strip() if " " in auth_value else ""
        api_key_user = api_key_store.verify(api_key)
        if api_key_user:
            return api_key_user

        raise _auth_error()

    session_user = _get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
    if session_user:
        return session_user

    raise _auth_error()


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _is_secure_request(request: Request) -> bool:
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


def require_session_user(request: Request) -> AuthenticatedUser:
    """仅允许管理页会话用户访问。"""
    if not _users_store.has_users_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No authentication users configured. Mount secrets/users.txt.",
        )

    session_user = _get_session_user(request.cookies.get(SESSION_COOKIE_NAME))
    if session_user:
        return session_user
    raise _auth_error()


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
async def get_session(_user: AuthenticatedUser = Depends(require_session_user)):
    """返回当前管理页会话状态。"""
    return {
        "authenticated": True,
        "username": _user.username,
        "source": _user.source,
    }


@router.get("/auth/api-keys")
async def list_api_keys(_user: AuthenticatedUser = Depends(require_session_user)):
    """列出当前用户创建的 API Key。"""
    return {"api_keys": api_key_store.list_keys(_user.username)}


@router.post("/auth/api-keys")
async def create_api_key(
    request_body: ApiKeyCreateRequest,
    _user: AuthenticatedUser = Depends(require_session_user),
):
    """创建新的 API Key，明文只在本次响应中返回。"""
    return api_key_store.create_key(_user.username, request_body.name)


@router.delete("/auth/api-keys/{key_id}")
async def delete_api_key(key_id: str, _user: AuthenticatedUser = Depends(require_session_user)):
    """删除当前用户的 API Key。"""
    if not api_key_store.delete_key(_user.username, key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"deleted": True}
