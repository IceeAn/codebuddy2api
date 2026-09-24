"""OpenAI 兼容协议的共享处理逻辑与鉴权隔离路由。"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .auth_router import require_api_key_user, require_session_user
from .auth_types import AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import CodeBuddyTokenManager, get_token_manager_for_user
from .chat_execution import (
    check_codebuddy_credential_availability,
    execute_codebuddy_chat,
    select_codebuddy_credential_with_id,
)
from .models_manager import models_manager
from .private_response import PrivateNoStoreRoute
from .request_processor import RequestProcessor
from .openai_schema import CONTENT_SCHEMA, EXTRA_REQUEST_PROPERTIES
from .responses_request import translate_responses_request
from .responses_response import ResponsesAdapter
from .stream_service import CodeBuddyStreamService, UpstreamAPIError
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
                                {
                                    "required": ["function_call"],
                                    "properties": {"role": {"const": "assistant"}},
                                },
                            ],
                            "additionalProperties": True,
                            "properties": {
                                "role": {"type": "string", "examples": ["system", "developer", "user", "assistant", "tool", "function"]},
                                "content": CONTENT_SCHEMA,
                                "name": {"type": "string"},
                                "tool_call_id": {"type": "string"},
                                "function_call": {"type": "object", "required": ["name", "arguments"],
                                                  "properties": {"name": {"type": "string"}, "arguments": {"type": "string"}}},
                                "audio": {"type": "object", "properties": {"id": {"type": "string"}}},
                                "reasoning_content": {"type": "string"},
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
                    **EXTRA_REQUEST_PROPERTIES,
                },
            }
        }
    },
}


async def get_available_models_list(user: AuthenticatedUser) -> List[str]:
    """动态加载可用模型列表，支持设置热更新和真实模型回退缓存。"""
    return await models_manager.get_available_models(user)


def get_valid_credential_selection(
        token_manager: CodeBuddyTokenManager,
) -> tuple[str, Dict[str, Any], int]:
    """原子选择凭证，并保持 OpenAI 入口的认证错误响应。"""
    try:
        return select_codebuddy_credential_with_id(token_manager)
    except Exception as error:
        logger.error("获取凭证失败: %s", error)
        raise HTTPException(status_code=401, detail="凭证获取失败") from error


async def chat_completions(
        request: Request,
        _user: AuthenticatedUser,
        stats_context: UsageStatsContext,
        x_conversation_id: Optional[str] = None,
        x_conversation_request_id: Optional[str] = None,
        x_conversation_message_id: Optional[str] = None,
        x_request_id: Optional[str] = None,
        request_bytes: Optional[int] = None,
        responses_mode: bool = False,
):
    """执行 OpenAI Chat Completions 兼容请求。"""
    try:
        stats_context.capture_request_bytes(request_bytes or 0)
        try:
            request_body = await request.json()
        except HTTPException:
            raise
        except Exception as e:
            logger.error("解析请求体失败: %s", type(e).__name__)
            stats_context.mark_failure("validation_error", 400)
            raise HTTPException(status_code=400, detail="Invalid JSON request body")

        if isinstance(request_body, dict):
            stats_context.capture_request_shape(request_body)

        try:
            if responses_mode:
                request_body, bindings = translate_responses_request(request_body)
            RequestProcessor.validate_request(request_body)
        except HTTPException as error:
            stats_context.mark_failure("validation_error", error.status_code)
            raise

        try:
            prepared_request = RequestProcessor.prepare_request(request_body, _user)
        except Exception as error:
            if isinstance(error, HTTPException) and error.status_code < 500:
                stats_context.mark_failure("validation_error", error.status_code)
                raise
            availability = check_codebuddy_credential_availability(
                _user,
                token_manager_factory=get_token_manager_for_user,
            )
            if availability is False:
                stats_context.mark_failure("no_credential", 401)
                raise HTTPException(status_code=401, detail="没有可用的CodeBuddy凭证") from error
            if isinstance(error, HTTPException):
                stats_context.mark_failure("internal_error", error.status_code)
            raise
        try:
            adapter_options = {"response_adapter": ResponsesAdapter(
                prepared_request.response_model, bindings,
                parallel_tool_calls=request_body.get("parallel_tool_calls", True),
            )} if responses_mode else {}
            return await execute_codebuddy_chat(
                prepared_request,
                _user,
                stats_context=stats_context,
                conversation_headers={
                    "conversation_id": x_conversation_id,
                    "conversation_request_id": x_conversation_request_id,
                    "conversation_message_id": x_conversation_message_id,
                    "request_id": x_request_id,
                },
                token_manager_factory=get_token_manager_for_user,
                credential_selector=get_valid_credential_selection,
                header_generator=codebuddy_api_client.generate_codebuddy_headers,
                service_factory=CodeBuddyStreamService,
                **adapter_options,
            )
        except HTTPException as error:
            if (
                    error.status_code == 401
                    and not isinstance(error, UpstreamAPIError)
            ):
                stats_context.mark_failure("no_credential", error.status_code)
            raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("OpenAI 兼容 API 错误: %s", type(e).__name__)
        stats_context.mark_failure("internal_error", 500)
        raise HTTPException(status_code=500, detail="内部服务器错误")


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
        "/v1/responses",
        name=f"{route_name_prefix}_responses",
        include_in_schema=include_in_schema,
        openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
            "type": "object", "required": ["model"], "additionalProperties": True,
            "description": "无状态 Responses；支持文字、图片、推理、客户端工具和历史回放。",
            "properties": {
                "model": {"type": "string"},
                "input": {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "object"}}]},
                "instructions": {"type": "string"}, "stream": {"type": "boolean", "default": False},
                "store": {"type": "boolean", "enum": [False], "default": False},
                "tools": {"type": "array", "items": {"type": "object"}},
                "tool_choice": {"oneOf": [{"type": "string"}, {"type": "object"}]},
                "reasoning": {"type": "object"}, "text": {"type": "object"},
                "max_output_tokens": {"type": "integer", "minimum": 1},
                "parallel_tool_calls": {"type": "boolean"},
            },
        }}}}},
    )
    async def responses_route(request: Request, _user: AuthenticatedUser = Depends(auth_dependency)):
        stats_context = create_usage_stats_context(request, _user, stats_source)
        request_bytes = len(await request.body())
        return await chat_completions(request, _user, stats_context=stats_context,
                                      request_bytes=request_bytes, responses_mode=True)

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
