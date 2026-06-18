"""CodeBuddy API Router - 提供 OpenAI 兼容 API。"""
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .auth_router import authenticate, require_session_user
from .auth_types import AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import get_token_manager_for_user
from .request_processor import RequestProcessor
from .stream_service import CodeBuddyStreamService
from .usage_stats_manager import usage_stats_manager

logger = logging.getLogger(__name__)
router = APIRouter()


def get_available_models_list() -> List[str]:
    """动态加载可用模型列表，支持设置热更新。"""
    from config import get_available_models

    return get_available_models()


class CredentialManager:
    """凭证管理器。"""

    @staticmethod
    def get_valid_credential(token_manager) -> Dict[str, Any]:
        """获取有效凭证，包含错误处理。"""
        try:
            credential = token_manager.get_next_credential()
            if not credential:
                raise HTTPException(status_code=401, detail="没有可用的CodeBuddy凭证")

            bearer_token = credential.get("bearer_token")
            if not bearer_token:
                raise HTTPException(status_code=401, detail="无效的CodeBuddy凭证")

            return credential
        except Exception as e:
            logger.error(f"获取凭证失败: {e}")
            raise HTTPException(status_code=401, detail="凭证获取失败")


@router.post("/v1/chat/completions")
async def chat_completions(
        request: Request,
        x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
        x_conversation_request_id: Optional[str] = Header(None, alias="X-Conversation-Request-ID"),
        x_conversation_message_id: Optional[str] = Header(None, alias="X-Conversation-Message-ID"),
        x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
        _user: AuthenticatedUser = Depends(authenticate),
):
    """CodeBuddy V1 聊天完成 API。"""
    try:
        try:
            request_body = await request.json()
        except Exception as e:
            logger.error(f"解析请求体失败: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid JSON request body: {str(e)}")

        RequestProcessor.validate_request(request_body)

        token_manager = get_token_manager_for_user(_user)
        credential = CredentialManager.get_valid_credential(token_manager)

        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=credential.get("bearer_token"),
            user_id=credential.get("user_id"),
            domain=credential.get("domain"),
            conversation_id=x_conversation_id,
            conversation_request_id=x_conversation_request_id,
            conversation_message_id=x_conversation_message_id,
            request_id=x_request_id,
        )

        payload = RequestProcessor.prepare_payload(request_body)
        usage_stats_manager.record_model_usage(payload.get("model", "unknown"))

        service = CodeBuddyStreamService()
        client_wants_stream = request_body.get("stream", False)

        if client_wants_stream:
            return await service.handle_stream_response(payload, headers)
        return await service.handle_non_stream_response(payload, headers)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CodeBuddy V1 API错误: {e}")
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")


@router.get("/v1/models")
async def list_v1_models(_user: AuthenticatedUser = Depends(authenticate)):
    """获取 CodeBuddy V1 模型列表。"""
    try:
        return {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "codebuddy",
                }
                for model in get_available_models_list()
            ],
        }

    except Exception as e:
        logger.error(f"获取V1模型列表错误: {e}")
        raise HTTPException(status_code=500, detail="获取模型列表失败")


@router.get("/v1/credentials", summary="List all available credentials")
async def list_credentials(_user: AuthenticatedUser = Depends(require_session_user)):
    """列出所有可用凭证的详细信息，包括过期状态。"""
    try:
        token_manager = get_token_manager_for_user(_user)
        credentials_info = token_manager.get_credentials_info()
        safe_credentials = []

        credentials = token_manager.get_all_credentials()

        for info in credentials_info:
            bearer_token = credentials[info["index"]].get("bearer_token", "") if info["index"] < len(
                credentials) else ""

            if info["time_remaining"] is not None and info["time_remaining"] > 0:
                days, remainder = divmod(info["time_remaining"], 86400)
                hours, remainder = divmod(remainder, 3600)
                minutes = remainder // 60
                time_remaining_str = (
                    f"{days}d {hours}h"
                    if days > 0
                    else f"{hours}h {minutes}m"
                    if hours > 0
                    else f"{minutes}m"
                )
            else:
                time_remaining_str = "Expired" if info["time_remaining"] is not None else "Unknown"

            safe_credentials.append(
                {
                    **info,
                    "time_remaining_str": time_remaining_str,
                    "has_token": bool(bearer_token),
                    "token_preview": f"{bearer_token[:10]}...{bearer_token[-4:]}"
                    if len(bearer_token) > 14
                    else "Invalid Token",
                }
            )

        return {"credentials": safe_credentials}

    except Exception as e:
        logger.error(f"获取凭证列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials", summary="Add a new credential")
async def add_credential(
        request: Request,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """添加一个新的认证凭证。"""
    try:
        data = await request.json()
        if not data.get("bearer_token"):
            raise HTTPException(status_code=422, detail="bearer_token is required")

        token_manager = get_token_manager_for_user(_user)
        success = token_manager.add_credential(
            data.get("bearer_token"),
            data.get("user_id"),
        )
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save credential file")

        return {"message": "Credential added successfully"}

    except Exception as e:
        logger.error(f"添加凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/select", summary="Manually select a credential")
async def select_credential(
        request: Request,
        _user: AuthenticatedUser = Depends(require_session_user),
):
    """手动选择指定的凭证。"""
    try:
        data = await request.json()
        index = data.get("index")
        if index is None:
            raise HTTPException(status_code=422, detail="index is required")

        token_manager = get_token_manager_for_user(_user)
        if not token_manager.set_manual_credential(index):
            raise HTTPException(status_code=400, detail="Invalid credential index")

        return {"message": f"Credential #{index + 1} selected successfully"}

    except Exception as e:
        logger.error(f"选择凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/auto", summary="Resume automatic credential rotation")
async def resume_auto_rotation(_user: AuthenticatedUser = Depends(require_session_user)):
    """恢复自动凭证轮换。"""
    try:
        token_manager = get_token_manager_for_user(_user)
        token_manager.clear_manual_selection()
        return {"message": "Resumed automatic credential rotation"}

    except Exception as e:
        logger.error(f"恢复自动轮换失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/toggle-rotation", summary="Toggle automatic credential rotation")
async def toggle_auto_rotation(_user: AuthenticatedUser = Depends(require_session_user)):
    """切换自动轮换开关。"""
    try:
        token_manager = get_token_manager_for_user(_user)
        is_enabled = token_manager.toggle_auto_rotation()
        status = "enabled" if is_enabled else "disabled"
        message = f"Auto rotation {status}"
        return {
            "message": message,
            "auto_rotation_enabled": is_enabled,
        }

    except Exception as e:
        logger.error(f"切换自动轮换失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/v1/credentials/current", summary="Get current credential info")
async def get_current_credential(_user: AuthenticatedUser = Depends(require_session_user)):
    """获取当前使用的凭证信息。"""
    try:
        token_manager = get_token_manager_for_user(_user)
        info = token_manager.get_current_credential_info()
        return info

    except Exception as e:
        logger.error(f"获取当前凭证信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/v1/credentials/delete", summary="Delete a credential by index")
async def delete_credential(request: Request, _user: AuthenticatedUser = Depends(require_session_user)):
    """删除一个凭证文件（通过索引）并从列表中移除。"""
    try:
        data = await request.json()
        index = data.get("index")
        if index is None or not isinstance(index, int):
            raise HTTPException(status_code=422, detail="Valid integer index is required")

        token_manager = get_token_manager_for_user(_user)
        if not token_manager.delete_credential_by_index(index):
            raise HTTPException(status_code=400, detail="Invalid index or failed to delete credential")

        return {"message": f"Credential #{index + 1} deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除凭证失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
