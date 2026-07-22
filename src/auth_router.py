"""服务自身的认证依赖和管理页认证路由。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from .api_key_store import api_key_store
from .auth_types import (
    SESSION_COOKIE_NAME,
    SESSION_REFRESH_STATE_KEY,
    SESSION_TTL_SECONDS,
    AuthenticatedUser,
    LoginRequest,
)
from .private_response import PrivateNoStoreRoute
from .login_security import LoginLimitError, login_attempt_guard
from .session_store import session_store
from .users_store import users_store

router = APIRouter(route_class=PrivateNoStoreRoute)
api_key_bearer = HTTPBearer(
    scheme_name="ApiKeyBearer",
    bearerFormat="sk-...",
    auto_error=False,
)
session_cookie = APIKeyCookie(
    name=SESSION_COOKIE_NAME,
    scheme_name="SessionCookie",
    auto_error=False,
)


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _login_auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="用户名或密码错误",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _is_secure_request(request: Request) -> bool:
    return request.url.scheme == "https"


def _require_users_file() -> None:
    if not users_store.has_users_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No authentication users configured. Mount secrets/users.txt.",
        )


def _verify_login_credentials(username: str, password: str) -> bool:
    """在线程中完成用户文件检查和昂贵密码校验。"""
    _require_users_file()
    return users_store.verify(username, password)


def _login_limit_error(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="登录尝试过于频繁，请稍后重试",
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


def require_api_key_user(
    request: Request,
    _credentials: Optional[HTTPAuthorizationCredentials] = Security(api_key_bearer),
) -> AuthenticatedUser:
    """仅允许通过 Bearer sk- API Key 访问外部客户端接口。"""
    auth_value = request.headers.get("Authorization", "")
    scheme = auth_value.split(" ", 1)[0].lower() if auth_value else ""

    _require_users_file()

    if scheme != "bearer":
        raise _auth_error()

    api_key = auth_value.split(" ", 1)[1].strip() if " " in auth_value else ""
    api_key_user = api_key_store.verify(api_key)
    if api_key_user:
        return api_key_user

    raise _auth_error()


def require_session_user(
    request: Request,
    _session_cookie: Optional[str] = Security(session_cookie),
) -> AuthenticatedUser:
    """仅允许管理页会话用户访问。"""
    _require_users_file()

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    session_user = session_store.get_user(session_id)
    if session_user:
        request.scope.setdefault("state", {})[SESSION_REFRESH_STATE_KEY] = {
            "session_id": session_id,
            "secure": _is_secure_request(request),
        }
        return session_user
    raise _auth_error()


@router.post("/auth/login")
async def login(request: Request, response: Response, credentials: LoginRequest):
    """登录管理页并写入 HttpOnly 会话 cookie。"""
    username = credentials.username.strip()
    client_ip = request.client.host if request.client is not None else None
    try:
        login_attempt_guard.record_attempt(client_ip, username)
    except LoginLimitError as error:
        raise _login_limit_error(error.retry_after) from error

    if not username or not credentials.password:
        raise _login_auth_error()

    if not login_attempt_guard.try_acquire():
        raise _login_limit_error(1)
    try:
        verified = await run_in_threadpool(
            _verify_login_credentials,
            username,
            credentials.password,
        )
    finally:
        login_attempt_guard.release()

    if not verified:
        raise _login_auth_error()

    session_id = session_store.create(username)
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
    session_store.invalidate(request.cookies.get(SESSION_COOKIE_NAME))
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
