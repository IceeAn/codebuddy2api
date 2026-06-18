"""服务自身的认证依赖和管理页认证路由。"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .api_key_store import api_key_store
from .auth_types import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    ApiKeyCreateRequest,
    AuthenticatedUser,
    LoginRequest,
)
from .session_store import session_store
from .users_store import users_store

router = APIRouter()


def _auth_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _is_secure_request(request: Request) -> bool:
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


def _require_users_file() -> None:
    if not users_store.has_users_file():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No authentication users configured. Mount secrets/users.txt.",
        )


def authenticate(request: Request) -> AuthenticatedUser:
    """验证用户身份，支持 Bearer sk- API Key 和管理页会话 cookie。"""
    auth_value = request.headers.get("Authorization", "")
    scheme = auth_value.split(" ", 1)[0].lower() if auth_value else ""

    _require_users_file()

    if scheme == "bearer":
        api_key = auth_value.split(" ", 1)[1].strip() if " " in auth_value else ""
        api_key_user = api_key_store.verify(api_key)
        if api_key_user:
            return api_key_user

        raise _auth_error()

    session_user = session_store.get_user(request.cookies.get(SESSION_COOKIE_NAME))
    if session_user:
        return session_user

    raise _auth_error()


def require_session_user(request: Request) -> AuthenticatedUser:
    """仅允许管理页会话用户访问。"""
    _require_users_file()

    session_user = session_store.get_user(request.cookies.get(SESSION_COOKIE_NAME))
    if session_user:
        return session_user
    raise _auth_error()


@router.post("/auth/login")
async def login(request: Request, response: Response, credentials: LoginRequest):
    """登录管理页并写入 HttpOnly 会话 cookie。"""
    _require_users_file()

    username = credentials.username.strip()
    if not username or not credentials.password:
        raise _auth_error()

    if not users_store.verify(username, credentials.password):
        raise _auth_error()

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
