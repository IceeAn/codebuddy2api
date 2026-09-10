"""服务自身的认证依赖、初始账号状态与密码修改路由。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from .api_key_store import api_key_store
from .auth_error_codes import AuthBusinessError, AuthErrorCode
from .auth_types import (
    SESSION_COOKIE_NAME,
    SESSION_DELETE_STATE_KEY,
    SESSION_REFRESH_STATE_KEY,
    SESSION_SUPPRESS_REFRESH_STATE_KEY,
    SESSION_TTL_SECONDS,
    AuthenticatedUser,
    ChangePasswordRequest,
    LoginRequest,
)
from .login_security import LoginLimitError, login_attempt_guard
from .password_hashing import create_password_hash
from .private_response import PrivateNoStoreRoute
from .session_store import session_store
from .users_store import UserRecord, users_store, validate_new_password

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


def _business_error(
    error_code: AuthErrorCode,
    status_code: int,
    *,
    authenticate: bool = False,
) -> AuthBusinessError:
    headers = {"WWW-Authenticate": "Bearer"} if authenticate else None
    return AuthBusinessError(status_code, error_code, headers=headers)


def _is_secure_request(request: Request) -> bool:
    return request.url.scheme == "https"


def _require_users() -> None:
    if not users_store.has_users():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No system users are configured in SQLite.",
        )


def _verify_login_credentials(username: str, password: str) -> Optional[UserRecord]:
    """在线程中完成 SQLite 账号读取和昂贵密码校验。"""
    _require_users()
    return users_store.verify_record(username, password)


def _login_limit_error(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="登录尝试过于频繁，请稍后重试",
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


def _password_limit_error(retry_after: int) -> AuthBusinessError:
    return AuthBusinessError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        AuthErrorCode.PASSWORD_CHANGE_RATE_LIMITED,
        headers={"Retry-After": str(max(1, int(retry_after)))},
    )


def _expire_cookie_on_final_response(request: Request) -> None:
    state_data = request.scope.setdefault("state", {})
    state_data[SESSION_SUPPRESS_REFRESH_STATE_KEY] = True
    state_data[SESSION_DELETE_STATE_KEY] = True


def _suppress_cookie_refresh(request: Request) -> None:
    request.scope.setdefault("state", {})[SESSION_SUPPRESS_REFRESH_STATE_KEY] = True


def require_api_key_user(
    request: Request,
    _credentials: Optional[HTTPAuthorizationCredentials] = Security(api_key_bearer),
) -> AuthenticatedUser:
    """仅允许通过 Bearer sk- API Key 访问外部客户端接口。"""
    auth_value = request.headers.get("Authorization", "")
    scheme = auth_value.split(" ", 1)[0].lower() if auth_value else ""

    _require_users()
    if scheme != "bearer":
        raise _auth_error()

    api_key = auth_value.split(" ", 1)[1].strip() if " " in auth_value else ""
    api_key_user = api_key_store.verify(api_key)
    if api_key_user:
        return api_key_user
    raise _auth_error()


def require_any_session_user(
    request: Request,
    _session_cookie: Optional[str] = Security(session_cookie),
) -> AuthenticatedUser:
    """允许正式会话和仍处于首次改密阶段的会话。"""
    _require_users()
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    session_user, invalid_reason = session_store.get_user_with_reason(session_id)
    if session_user:
        request.scope.setdefault("state", {})[SESSION_REFRESH_STATE_KEY] = {
            "session_id": session_id,
            "secure": _is_secure_request(request),
        }
        return session_user
    if invalid_reason == AuthErrorCode.BOOTSTRAP_EXPIRED.value:
        _expire_cookie_on_final_response(request)
        raise _business_error(
            AuthErrorCode.BOOTSTRAP_EXPIRED,
            status.HTTP_401_UNAUTHORIZED,
            authenticate=True,
        )
    if invalid_reason == AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE.value:
        _expire_cookie_on_final_response(request)
        raise _business_error(
            AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE,
            status.HTTP_401_UNAUTHORIZED,
            authenticate=True,
        )
    raise _auth_error()


def require_session_user(
    request: Request,
    _session_cookie: Optional[str] = Security(session_cookie),
) -> AuthenticatedUser:
    """仅允许已完成首次改密的管理页会话。"""
    session_user = require_any_session_user(request, _session_cookie)
    if session_user.password_change_required:
        raise _business_error(
            AuthErrorCode.PASSWORD_CHANGE_REQUIRED,
            status.HTTP_403_FORBIDDEN,
        )
    return session_user


@router.get("/auth/bootstrap-status")
async def get_bootstrap_status():
    """公开返回是否需要初始登录以及初始账号是否已过期。"""
    bootstrap_status = users_store.bootstrap_status()
    return {
        "bootstrap_required": bootstrap_status.bootstrap_required,
        "bootstrap_expired": bootstrap_status.bootstrap_expired,
    }


@router.post("/auth/login")
async def login(request: Request, response: Response, credentials: LoginRequest):
    """登录管理页并写入 HttpOnly 会话 Cookie。"""
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
        record = await run_in_threadpool(
            _verify_login_credentials,
            username,
            credentials.password,
        )
    finally:
        login_attempt_guard.release()

    if record is None:
        raise _login_auth_error()
    if record.password_change_required and users_store.is_bootstrap_expired(record.username):
        _expire_cookie_on_final_response(request)
        raise _business_error(
            AuthErrorCode.BOOTSTRAP_EXPIRED,
            status.HTTP_401_UNAUTHORIZED,
            authenticate=True,
        )

    session_id = session_store.create(record.username, record)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
        path="/",
    )
    return {
        "authenticated": True,
        "username": record.username,
        "source": "session_cookie",
        "password_change_required": record.password_change_required,
    }


@router.post("/auth/logout")
async def logout(request: Request, response: Response):
    """退出管理页登录并清理会话 Cookie。"""
    session_store.invalidate(request.cookies.get(SESSION_COOKIE_NAME))
    _suppress_cookie_refresh(request)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=_is_secure_request(request),
        httponly=True,
        samesite="lax",
    )
    return {"authenticated": False}


@router.get("/auth/session")
async def get_session(_user: AuthenticatedUser = Depends(require_any_session_user)):
    """返回当前管理页会话状态，包含首次改密标志。"""
    return {
        "authenticated": True,
        "username": _user.username,
        "source": _user.source,
        "password_change_required": _user.password_change_required,
    }


def _prepare_password_hash(
    user: AuthenticatedUser,
    current_password: Optional[str],
    new_password: str,
) -> tuple[Optional[AuthErrorCode], Optional[str]]:
    """在线程中校验当前密码/复用并生成新哈希。"""
    if user.password_change_required:
        if users_store.verify(user.username, new_password):
            return AuthErrorCode.NEW_PASSWORD_UNCHANGED, None
    else:
        if not users_store.verify(user.username, current_password or ""):
            return AuthErrorCode.CURRENT_PASSWORD_INCORRECT, None
        if current_password == new_password:
            return AuthErrorCode.NEW_PASSWORD_UNCHANGED, None
    return None, create_password_hash(new_password)


@router.post("/auth/change-password")
async def change_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    user: AuthenticatedUser = Depends(require_any_session_user),
):
    """修改当前账号密码；成功后包括当前会话在内全部退出。"""
    current_password = payload.current_password
    if user.password_change_required:
        if current_password:
            raise _business_error(
                AuthErrorCode.CURRENT_PASSWORD_NOT_ALLOWED,
                status.HTTP_400_BAD_REQUEST,
            )
    elif not current_password:
        raise _business_error(
            AuthErrorCode.CURRENT_PASSWORD_REQUIRED,
            status.HTTP_400_BAD_REQUEST,
        )

    try:
        validate_new_password(payload.new_password, minimum=8)
    except ValueError as error:
        raise _business_error(
            AuthErrorCode.NEW_PASSWORD_INVALID,
            status.HTTP_400_BAD_REQUEST,
        ) from error

    client_ip = request.client.host if request.client is not None else None
    try:
        login_attempt_guard.record_attempt(client_ip, user.username)
    except LoginLimitError as error:
        raise _password_limit_error(error.retry_after) from error
    if not login_attempt_guard.try_acquire():
        raise _password_limit_error(1)
    try:
        preparation_error, password_hash = await run_in_threadpool(
            _prepare_password_hash,
            user,
            current_password,
            payload.new_password,
        )
    finally:
        login_attempt_guard.release()

    if preparation_error is not None:
        raise _business_error(preparation_error, status.HTTP_400_BAD_REQUEST)
    if await request.is_disconnected():
        raise HTTPException(status_code=499, detail="客户端已断开，密码未修改")

    changed = await run_in_threadpool(
        users_store.replace_password,
        user.username,
        expected_revision=user.auth_revision or b"",
        password_hash=password_hash or "",
    )
    if changed is None:
        _expire_cookie_on_final_response(request)
        raise _business_error(
            AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE,
            status.HTTP_401_UNAUTHORIZED,
            authenticate=True,
        )

    session_store.revoke_user(
        user.username,
        AuthErrorCode.PASSWORD_CHANGED_ELSEWHERE.value,
    )
    _suppress_cookie_refresh(request)
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=_is_secure_request(request),
        httponly=True,
        samesite="lax",
    )
    return {"password_changed": True, "authenticated": False}
