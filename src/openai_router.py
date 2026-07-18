"""OpenAI 兼容协议的共享处理逻辑与鉴权隔离路由。"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .auth_router import require_api_key_user, require_session_user
from .auth_types import AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import get_token_manager_for_user
from .models_manager import models_manager
from .private_response import PrivateNoStoreRoute
from .request_processor import RequestProcessor
from .stream_service import CodeBuddyStreamService
from .usage_stats_context import UsageStatsContext, create_usage_stats_context

logger = logging.getLogger(__name__)


CHAT_COMPLETIONS_OPENAPI_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["messages"],
                "additionalProperties": True,
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "模型名称；省略时使用服务端默认模型。",
                    },
                    "messages": {
                        "type": "array",
                        "minItems": 1,
                        "description": "OpenAI Chat Completions 消息列表。",
                        "items": {
                            "type": "object",
                            "required": ["role"],
                            "anyOf": [
                                {"required": ["content"]},
                                {
                                    "required": ["tool_calls"],
                                    "properties": {"role": {"const": "assistant"}},
                                },
                            ],
                            "additionalProperties": True,
                            "properties": {
                                "role": {"type": "string"},
                                "content": {},
                                "tool_calls": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "object"},
                                },
                            },
                        },
                    },
                    "stream": {"type": "boolean", "default": False},
                    "temperature": {
                        "anyOf": [{"type": "number"}, {"type": "null"}],
                    },
                    "max_tokens": {
                        "anyOf": [{"type": "integer"}, {"type": "null"}],
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": True},
                    },
                    "tool_choice": {
                        "anyOf": [{"type": "string"}, {"type": "object"}],
                    },
                    "reasoning_effort": {"type": "string"},
                    "thinking": {"type": "object", "additionalProperties": True},
                    "enable_thinking": {"type": "boolean"},
                },
            }
        }
    },
}


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
            logger.error("获取凭证失败: %s", e)
            raise HTTPException(status_code=401, detail="凭证获取失败")


async def chat_completions(
        request: Request,
        _user: AuthenticatedUser,
        x_conversation_id: Optional[str] = None,
        x_conversation_request_id: Optional[str] = None,
        x_conversation_message_id: Optional[str] = None,
        x_request_id: Optional[str] = None,
        stats_context: Optional[UsageStatsContext] = None,
        request_bytes: Optional[int] = None,
):
    """执行 OpenAI Chat Completions 兼容请求。"""
    try:
        if stats_context is not None:
            stats_context.capture_request_bytes(request_bytes or 0)
        try:
            request_body = await request.json()
        except Exception as e:
            logger.error("解析请求体失败: %s", e)
            if stats_context is not None:
                stats_context.mark_failure("validation_error", 400)
            raise HTTPException(status_code=400, detail=f"Invalid JSON request body: {str(e)}")

        if stats_context is not None and isinstance(request_body, dict):
            stats_context.capture_request_shape(request_body)

        try:
            RequestProcessor.validate_request(request_body)
        except HTTPException as error:
            if stats_context is not None:
                stats_context.mark_failure("validation_error", error.status_code)
            raise

        token_manager = get_token_manager_for_user(_user)
        try:
            credential = CredentialManager.get_valid_credential(token_manager)
        except HTTPException as error:
            if stats_context is not None:
                stats_context.mark_failure("no_credential", error.status_code)
            raise
        if stats_context is not None:
            current_info = token_manager.get_current_credential_info()
            credential_id = current_info.get("credential_id")
            credential_label = (
                current_info.get("filename")
                or current_info.get("user_id")
                or credential_id
            )
            stats_context.capture_credential(credential_id, credential_label)

        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=credential.get("bearer_token"),
            user_id=credential.get("user_id"),
            account_uid=credential.get("account_uid"),
            domain=credential.get("domain"),
            enterprise_id=credential.get("enterprise_id"),
            department_full_name=credential.get("department_full_name"),
            conversation_id=x_conversation_id,
            conversation_request_id=x_conversation_request_id,
            conversation_message_id=x_conversation_message_id,
            request_id=x_request_id,
        )

        prepared_request = RequestProcessor.prepare_request(request_body, _user)
        payload = prepared_request.payload
        if stats_context is not None:
            stats_context.capture_prepared_request(payload)

        service = CodeBuddyStreamService(observer=stats_context)

        if prepared_request.client_wants_stream:
            return await service.handle_stream_response(
                payload,
                headers,
                response_model=prepared_request.response_model,
            )
        return await service.handle_non_stream_response(
            payload,
            headers,
            response_model=prepared_request.response_model,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("OpenAI 兼容 API 错误: %s", e)
        if stats_context is not None:
            stats_context.mark_failure("internal_error", 500)
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")


async def list_v1_models(user: AuthenticatedUser):
    """获取 OpenAI V1 兼容模型列表。"""
    try:
        models = await get_available_models_list(user)
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
        logger.error("获取V1模型列表错误: %s", e)
        raise HTTPException(status_code=500, detail="获取模型列表失败")


def create_openai_compatible_router(
        auth_dependency: Callable[..., AuthenticatedUser],
        route_name_prefix: str,
        stats_source: str,
        include_in_schema: bool = True,
) -> APIRouter:
    """创建共享协议行为、使用指定认证方式的 OpenAI 兼容路由。"""
    router = APIRouter(route_class=PrivateNoStoreRoute)

    @router.post(
        "/v1/chat/completions",
        name=f"{route_name_prefix}_chat_completions",
        include_in_schema=include_in_schema,
        openapi_extra={"requestBody": CHAT_COMPLETIONS_OPENAPI_REQUEST_BODY},
    )
    async def chat_completions_route(
            request: Request,
            x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
            x_conversation_request_id: Optional[str] = Header(None, alias="X-Conversation-Request-ID"),
            x_conversation_message_id: Optional[str] = Header(None, alias="X-Conversation-Message-ID"),
            x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
            _user: AuthenticatedUser = Depends(auth_dependency),
    ):
        stats_context = create_usage_stats_context(request, _user, stats_source)
        request_bytes = len(await request.body())
        return await chat_completions(
            request,
            _user=_user,
            x_conversation_id=x_conversation_id,
            x_conversation_request_id=x_conversation_request_id,
            x_conversation_message_id=x_conversation_message_id,
            x_request_id=x_request_id,
            stats_context=stats_context,
            request_bytes=request_bytes,
        )

    @router.get(
        "/v1/models",
        name=f"{route_name_prefix}_list_models",
        include_in_schema=include_in_schema,
    )
    async def list_v1_models_route(
            _user: AuthenticatedUser = Depends(auth_dependency),
    ):
        return await list_v1_models(_user)

    return router


external_openai_router = create_openai_compatible_router(
    require_api_key_user,
    "external_openai",
    "external_api",
)
playground_openai_router = create_openai_compatible_router(
    require_session_user,
    "playground_openai",
    "admin_playground",
    include_in_schema=False,
)
