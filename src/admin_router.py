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
from .credential_refresh import CredentialRefreshError, credential_refresh_manager
from .credential_quota import credential_quota_manager
from .models_manager import models_manager
from .private_response import PrivateNoStoreRoute
from .request_processor import RequestProcessor
from .stream_service import CodeBuddyStreamService
from .usage_stats_context import UsageStatsContext, create_usage_stats_context

logger = logging.getLogger(__name__)
router = APIRouter(route_class=PrivateNoStoreRoute)
SERVICE_STARTED_MONOTONIC = time.monotonic()

SETTING_FIELDS: List[Dict[str, Any]] = [
    {
        "key": "CODEBUDDY_MODELS",
        "label": "附加模型列表",
        "type": "tags",
        "separator": ",",
        "description": (
            "手动补充可用模型 ID，与 CodeBuddy 配置接口返回的真实模型合并作为模型列表；"
            "配置项优先展示，并作为客户端省略 model 时的默认候选。"
        ),
    },
    {
        "key": "CODEBUDDY_FORCED_REASONING_MODELS",
        "label": "强制推理模型列表",
        "type": "tags",
        "separator": ",",
        "description": (
            "命中的模型在转发上游前会强制 reasoning_effort=max 并启用 thinking.type=enabled；"
            "匹配时忽略 provider/ 前缀和大小写。"
        ),
    },
    {
        "key": "CODEBUDDY_FORCED_TEMPERATURE",
        "label": "强制 temperature",
        "type": "number",
        "nullable": True,
        "min": 0,
        "max": 2,
        "step": 0.1,
        "description": "非空时覆盖客户端传入的 temperature；留空则不覆盖客户端行为。",
    },
    {
        "key": "CODEBUDDY_STRIP_MODEL_NAMESPACE",
        "label": "去除模型名前缀",
        "type": "boolean",
        "description": "开启后将 provider/model 形式的模型名转发为 model，用于兼容上游模型 ID。",
    },
    {
        "key": "CODEBUDDY_AUTO_ROTATION_ENABLED",
        "label": "凭证轮换",
        "type": "boolean",
        "description": "开启后在有效凭证之间按轮换频率自动切换；关闭后固定当前凭证，手动选择凭证也会关闭自动轮换。",
    },
    {
        "key": "CODEBUDDY_ROTATION_COUNT",
        "label": "轮换频率",
        "type": "number",
        "min": 1,
        "step": 1,
        "description": "自动轮换开启时，每张凭证最多连续使用的请求次数；达到次数后切换到下一张未过期凭证，必须为正整数。",
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


def _safe_credentials(token_manager, username: Optional[str] = None) -> List[Dict[str, Any]]:
    credentials = token_manager.get_all_credentials()
    result = []
    for info in token_manager.get_credentials_info():
        index = info.get("index")
        credential = credentials[index] if isinstance(index, int) and index < len(credentials) else None
        safe = _safe_credential(info, credential)
        owner = username or getattr(token_manager, "username", None)
        if owner:
            safe["quota"] = credential_quota_manager.get_quota(
                owner, info["credential_id"],
            )
        result.append(safe)
    return result


def _credential_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Credential not found")


def get_stream_service_factory() -> Callable[[], CodeBuddyStreamService]:
    return CodeBuddyStreamService


def _request_base_url(request: Request) -> str:
    scheme = request.url.scheme or "http"
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
        "credentials": _safe_credentials(token_manager, _user.username),
        "current": token_manager.get_current_credential_info(),
    }


@router.get("/credentials/{credential_id}/accounts")
async def list_credential_accounts(
        credential_id: str,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """列出凭证可切换账号，不返回真实账号或企业 ID。"""
    token_manager = get_token_manager_for_user(_user)
    credential = token_manager.get_credential_by_id(credential_id)
    if credential is None:
        raise _credential_not_found()
    current_account_id = credential.get("account_id")
    safe_accounts = []
    for account in credential.get("accounts") or []:
        if not isinstance(account, dict) or not account.get("account_id"):
            continue
        safe_accounts.append({
            "account_id": account["account_id"],
            "type": account.get("type"),
            "nickname": account.get("nickname"),
            "enterprise_name": account.get("enterprise_name"),
            "department_full_name": account.get("department_full_name"),
            "is_current": account["account_id"] == current_account_id,
        })
    return {
        "accounts": safe_accounts,
        "current_account_id": current_account_id,
        "can_switch": bool(credential.get("refresh_token")) and len(safe_accounts) > 1,
    }


@router.post("/credentials/{credential_id}/accounts/{account_id}/select")
async def select_credential_account(
        credential_id: str,
        account_id: str,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """切换 OAuth 凭证的个人或企业账号。"""
    token_manager = get_token_manager_for_user(_user)
    if token_manager.get_credential_by_id(credential_id) is None:
        raise _credential_not_found()
    try:
        selected = await credential_refresh_manager.switch_account(
            _user.username,
            token_manager,
            credential_id,
            account_id,
        )
    except CredentialRefreshError as error:
        reason = str(error)
        if reason in {"credential_not_found", "account_not_found"}:
            raise HTTPException(status_code=404, detail="Credential account not found") from error
        if reason in {"switch_unavailable", "account_invalid", "account_missing", "generation_conflict"}:
            raise HTTPException(status_code=409, detail="Credential account cannot be switched") from error
        if reason == "ip_restricted":
            raise HTTPException(status_code=403, detail="Current IP is restricted by CodeBuddy") from error
        if reason == "temporary":
            raise HTTPException(status_code=503, detail="CodeBuddy account service unavailable") from error
        raise HTTPException(status_code=502, detail="CodeBuddy account switch failed") from error
    if selected:
        credential_quota_manager.invalidate_credential(_user.username, credential_id)
        credential_quota_manager.schedule_probe_if_running(
            _user.username, token_manager, credential_id,
        )
    return {"selected": bool(selected), "credential_id": credential_id, "account_id": account_id}


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

    for credential in _safe_credentials(token_manager, _user.username):
        if credential["credential_id"] not in before_ids:
            credential_quota_manager.schedule_probe_if_running(
                _user.username, token_manager, credential["credential_id"],
            )
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
    models_manager.invalidate_credential(_user, credential_id)
    credential_quota_manager.invalidate_credential(_user.username, credential_id)
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


async def test_admin_credential(
        credential_id: str,
        request_body: CredentialTestRequest,
        _user: AuthenticatedUser = Depends(require_session_user),
        stream_service_factory: Callable[[], CodeBuddyStreamService] = Depends(get_stream_service_factory),
        stats_context: Optional[UsageStatsContext] = None,
):
    """使用指定凭证发起最小请求，验证该凭证是否可用。"""
    token_manager = get_token_manager_for_user(_user)
    if stats_context is not None:
        stats_context.capture_credential(credential_id, credential_id)
    credential = token_manager.get_credential_by_id(credential_id)
    if not credential:
        if stats_context is not None:
            stats_context.mark_failure("credential_not_found", 404)
        raise _credential_not_found()

    if stats_context is not None:
        credential_info = next(
            (
                item for item in token_manager.get_credentials_info()
                if item.get("credential_id") == credential_id
            ),
            {},
        )
        label = (
            credential_info.get("filename")
            or credential_info.get("user_id")
            or credential_id
        )
        stats_context.capture_credential(credential_id, label)

    try:
        model = await models_manager.get_first_actual_model_for_credential(_user, credential_id, credential)
        model_source = "actual"
    except Exception as error:
        logger.warning("Credential model lookup failed (%s)", type(error).__name__)
        configured_models = [
            model_id
            for model_id in config.get_available_models(_user)
            if str(model_id).strip()
        ]
        if not configured_models:
            if stats_context is not None:
                stats_context.mark_failure("model_lookup", 502)
            return {
                "ok": False,
                "status_code": 502,
                "detail": "无法获取凭证模型",
            }
        model = configured_models[0]
        model_source = "configured_fallback"
    if stats_context is not None:
        stats_context.capture_confirmed_model(model)

    test_request = {
        "model": model,
        "messages": [{"role": "user", "content": request_body.message or "test"}],
        "max_tokens": 1,
    }
    if stats_context is not None:
        stats_context.capture_request_shape(test_request)
    prepared_request = RequestProcessor.prepare_request(test_request, _user)
    payload = prepared_request.payload
    if stats_context is not None:
        stats_context.capture_prepared_request(payload)
    headers = codebuddy_api_client.generate_codebuddy_headers(
        bearer_token=credential.get("bearer_token"),
        user_id=credential.get("user_id"),
        account_uid=credential.get("account_uid"),
        domain=credential.get("domain"),
        enterprise_id=credential.get("enterprise_id"),
        department_full_name=credential.get("department_full_name"),
    )

    try:
        service = (
            stream_service_factory(observer=stats_context)
            if stats_context is not None
            else stream_service_factory()
        )
        await service.handle_non_stream_response(
            payload,
            headers,
            response_model=prepared_request.response_model,
        )
        if stats_context is not None:
            stats_context.mark_success()
        return {"ok": True, "status_code": 200, "model_source": model_source}
    except HTTPException as e:
        if stats_context is not None:
            error = getattr(e, "error", None)
            error_type = error.get("type") if isinstance(error, dict) else "upstream_error"
            stats_context.mark_failure(error_type, e.status_code)
        return {
            "ok": False,
            "status_code": e.status_code,
            "detail": e.detail,
            "model_source": model_source,
        }
    except Exception:
        logger.exception("Credential test failed")
        if stats_context is not None:
            stats_context.mark_failure("internal_error", 500)
        return {
            "ok": False,
            "status_code": 500,
            "detail": "凭证测试失败",
            "model_source": model_source,
        }


async def get_credential_test_stats_context(
        credential_id: str,
        request: Request,
        _user: AuthenticatedUser = Depends(require_session_user),
) -> UsageStatsContext:
    """认证通过后立即创建凭证测试统计上下文。"""
    context = create_usage_stats_context(request, _user, "credential_test")
    context.capture_request_bytes(len(await request.body()))
    context.capture_credential(credential_id, credential_id)
    return context


@router.post("/credentials/{credential_id}/test")
async def test_admin_credential_route(
        credential_id: str,
        request_body: CredentialTestRequest,
        _user: AuthenticatedUser = Depends(require_session_user),
        stream_service_factory: Callable[[], CodeBuddyStreamService] = Depends(get_stream_service_factory),
        stats_context: UsageStatsContext = Depends(get_credential_test_stats_context),
):
    """创建统计上下文后执行一次管理台凭证测试。"""
    return await test_admin_credential(
        credential_id,
        request_body,
        _user,
        stream_service_factory,
        stats_context,
    )


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
