"""管理页专用 API 路由。"""
import logging
import time
from typing import Annotated, Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StringConstraints

import config
from .api_key_store import api_key_store
from .auth_router import require_session_user
from .auth_types import ApiKeyCreateRequest, AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import get_token_manager_for_user
from .models_manager import models_manager
from .private_response import PrivateNoStoreRoute
from .request_processor import RequestProcessor
from .stream_service import CodeBuddyStreamService
from .usage_stats_manager import usage_stats_manager

logger = logging.getLogger(__name__)
router = APIRouter(route_class=PrivateNoStoreRoute)
SERVICE_STARTED_MONOTONIC = time.monotonic()

SETTING_FIELDS: List[Dict[str, Any]] = [
    {
        "key": "CODEBUDDY_MODELS",
        "label": "附加模型列表",
        "type": "tags",
        "separator": ",",
    },
    {
        "key": "CODEBUDDY_FORCED_REASONING_MODELS",
        "label": "强制推理模型列表",
        "type": "tags",
        "separator": ",",
    },
    {
        "key": "CODEBUDDY_FORCED_TEMPERATURE",
        "label": "强制 temperature",
        "type": "number",
        "nullable": True,
        "min": 0,
        "max": 2,
        "step": 0.1,
    },
    {
        "key": "CODEBUDDY_STRIP_MODEL_NAMESPACE",
        "label": "去除模型名前缀",
        "type": "boolean",
    },
    {
        "key": "CODEBUDDY_AUTO_ROTATION_ENABLED",
        "label": "凭证轮换",
        "type": "boolean",
    },
    {
        "key": "CODEBUDDY_ROTATION_COUNT",
        "label": "轮换频率",
        "type": "number",
        "min": 1,
        "step": 1,
    },
]


class AdminSettingsUpdate(BaseModel):
    settings: Dict[str, Any]


class CredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bearer_token: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, pattern=r"\S"),
    ]


class CredentialTestRequest(BaseModel):
    message: str = "test"


def _time_remaining_text(seconds: Optional[int]) -> str:
    if seconds is None:
        return "Unknown"
    if seconds <= 0:
        return "Expired"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _safe_credential(info: Dict[str, Any], credential: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    bearer_token = (credential or {}).get("bearer_token", "")
    token_display = (
        f"{bearer_token[:6]}...{bearer_token[-8:]}"
        if len(bearer_token) > 14
        else "********" if bearer_token else ""
    )
    safe_info = {
        key: value
        for key, value in info.items()
        if key != "index"
    }
    safe_info["time_remaining_str"] = _time_remaining_text(info.get("time_remaining"))
    safe_info["has_token"] = bool(bearer_token)
    safe_info["token_display"] = token_display
    return safe_info


def _safe_credentials(token_manager) -> List[Dict[str, Any]]:
    credentials = token_manager.get_all_credentials()
    result = []
    for info in token_manager.get_credentials_info():
        index = info.get("index")
        credential = credentials[index] if isinstance(index, int) and index < len(credentials) else None
        result.append(_safe_credential(info, credential))
    return result


def _credential_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Credential not found")


def get_stream_service_factory() -> Callable[[], CodeBuddyStreamService]:
    return CodeBuddyStreamService


def _request_base_url(request: Request) -> str:
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or request.url.scheme or "http"
    host = request.headers.get("host") or request.url.hostname or "testserver"
    return f"{scheme}://{host}".rstrip("/")


def _service_uptime_seconds() -> int:
    return int(time.monotonic() - SERVICE_STARTED_MONOTONIC)


def _editable_settings_response(user: AuthenticatedUser) -> Dict[str, Any]:
    return config.get_editable_config(user).copy()


@router.get("/status")
async def get_admin_status(
        request: Request,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """返回管理台首页所需的真实服务状态。"""
    token_manager = get_token_manager_for_user(_user)
    credentials = token_manager.get_credentials_info()
    valid_count = len([item for item in credentials if not item.get("is_expired")])
    base_url = _request_base_url(request)

    return {
        "service": "CodeBuddy2API",
        "status": "healthy",
        "username": _user.username,
        "source": _user.source,
        "uptime_seconds": _service_uptime_seconds(),
        "api_base_url": f"{base_url}/openai/v1",
        "credentials": {
            "total": len(credentials),
            "valid": valid_count,
            "current": token_manager.get_current_credential_info(),
        },
        "usage": usage_stats_manager.get_stats(_user.username),
    }


@router.get("/api-keys")
async def list_admin_api_keys(_user: AuthenticatedUser = Depends(require_session_user)):
    """列出当前管理页用户的 API Key。"""
    return {"api_keys": api_key_store.list_keys(_user.username)}


@router.post("/api-keys")
async def create_admin_api_key(
        request_body: ApiKeyCreateRequest,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """创建当前管理页用户的 API Key。"""
    return api_key_store.create_key(_user.username, request_body.name)


@router.delete("/api-keys/{key_id}")
async def delete_admin_api_key(
        key_id: str,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """删除当前管理页用户的 API Key。"""
    if not api_key_store.delete_key(_user.username, key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return {"deleted": True}


@router.get("/credentials")
async def list_admin_credentials(_user: AuthenticatedUser = Depends(require_session_user)):
    """列出当前管理页用户的 CodeBuddy 凭证。"""
    token_manager = get_token_manager_for_user(_user)
    return {
        "credentials": _safe_credentials(token_manager),
        "current": token_manager.get_current_credential_info(),
    }


@router.post("/credentials")
async def create_admin_credential(
        request_body: CredentialCreateRequest,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """手动添加当前管理页用户的 CodeBuddy 凭证。"""
    bearer_token = request_body.bearer_token

    token_manager = get_token_manager_for_user(_user)
    before_ids = {item["credential_id"] for item in token_manager.get_credentials_info()}
    if not token_manager.add_credential(bearer_token):
        raise HTTPException(status_code=500, detail="Failed to save credential file")

    for credential in _safe_credentials(token_manager):
        if credential["credential_id"] not in before_ids:
            return {"credential": credential}
    raise HTTPException(status_code=500, detail="Failed to identify newly created credential")


@router.post("/credentials/{credential_id}/select")
async def select_admin_credential(
        credential_id: str,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """选择当前管理页用户的一张凭证，并关闭自动轮换。"""
    token_manager = get_token_manager_for_user(_user)
    auto_rotation_was_enabled = (
        token_manager.get_current_credential_info().get("auto_rotation_enabled") is True
    )
    if not token_manager.set_current_credential_by_id(credential_id):
        raise _credential_not_found()
    return {
        "auto_rotation_disabled_by_select": auto_rotation_was_enabled,
        "current": token_manager.get_current_credential_info(),
    }


@router.delete("/credentials/{credential_id}")
async def delete_admin_credential(
        credential_id: str,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """删除当前管理页用户的一张凭证。"""
    token_manager = get_token_manager_for_user(_user)
    if not token_manager.delete_credential_by_id(credential_id):
        raise _credential_not_found()
    return {"deleted": True, "current": token_manager.get_current_credential_info()}


@router.post("/credentials/rotation/toggle")
async def toggle_admin_auto_rotation(_user: AuthenticatedUser = Depends(require_session_user)):
    """切换当前管理页用户的自动凭证轮换。"""
    token_manager = get_token_manager_for_user(_user)
    auto_rotation_enabled = token_manager.toggle_auto_rotation()
    return {
        "message": "自动轮换已启用。" if auto_rotation_enabled else "自动轮换已关闭。",
        "auto_rotation_enabled": auto_rotation_enabled,
        "current": token_manager.get_current_credential_info(),
    }


@router.post("/credentials/{credential_id}/test")
async def test_admin_credential(
        credential_id: str,
        request_body: CredentialTestRequest,
        _user: AuthenticatedUser = Depends(require_session_user),
        stream_service_factory: Callable[[], CodeBuddyStreamService] = Depends(get_stream_service_factory),
):
    """使用指定凭证发起最小请求，验证该凭证是否可用。"""
    token_manager = get_token_manager_for_user(_user)
    credential = token_manager.get_credential_by_id(credential_id)
    if not credential:
        raise _credential_not_found()

    try:
        model = await models_manager.get_first_actual_model_for_credential(_user, credential_id, credential)
    except Exception as e:
        logger.warning("Credential model lookup failed: %s", e)
        return {
            "ok": False,
            "status_code": 502,
            "detail": str(e),
        }

    prepared_request = RequestProcessor.prepare_request(
        {
            "model": model,
            "messages": [{"role": "user", "content": request_body.message or "test"}],
            "max_tokens": 1,
        },
        _user,
    )
    payload = prepared_request.payload
    headers = codebuddy_api_client.generate_codebuddy_headers(
        bearer_token=credential.get("bearer_token"),
        user_id=credential.get("user_id"),
        domain=credential.get("domain"),
        enterprise_id=credential.get("enterprise_id"),
    )

    try:
        await stream_service_factory().handle_non_stream_response(
            payload,
            headers,
            response_model=prepared_request.response_model,
        )
        return {"ok": True, "status_code": 200}
    except HTTPException as e:
        return {
            "ok": False,
            "status_code": e.status_code,
            "detail": e.detail,
        }
    except Exception as e:
        logger.warning("Credential test failed: %s", e)
        return {
            "ok": False,
            "status_code": 500,
            "detail": str(e),
        }


@router.get("/settings")
async def get_admin_settings(_user: AuthenticatedUser = Depends(require_session_user)):
    """返回可热更新设置和字段描述。"""
    return {
        "settings": _editable_settings_response(_user),
        "fields": SETTING_FIELDS,
    }


@router.put("/settings")
async def save_admin_settings(
        request_body: AdminSettingsUpdate,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """保存可热更新设置。"""
    try:
        config.update_settings(request_body.settings, _user)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "message": "设置已保存并热加载。",
        "settings": _editable_settings_response(_user),
        "fields": SETTING_FIELDS,
    }
