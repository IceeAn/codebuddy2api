"""CodeBuddy API Router - 提供 OpenAI 兼容 API。"""
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .auth_router import authenticate
from .auth_types import AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import get_token_manager_for_user
from .models_manager import models_manager
from .request_processor import RequestProcessor
from .stream_service import CodeBuddyStreamService
from .usage_stats_manager import usage_stats_manager

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_available_models_list(user: AuthenticatedUser) -> List[str]:
    """动态加载可用模型列表，支持设置热更新和真实模型回退缓存。"""
    return await models_manager.get_available_models(user)


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

        payload = RequestProcessor.prepare_payload(request_body, _user)
        usage_stats_manager.record_model_usage(_user.username, payload.get("model", "unknown"))

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
        models = await get_available_models_list(_user)
        return {
            "object": "list",
            "data": [
                {
                    "id": model,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "codebuddy",
                }
                for model in models
            ],
        }

    except Exception as e:
        logger.error(f"获取V1模型列表错误: {e}")
        raise HTTPException(status_code=500, detail="获取模型列表失败")
