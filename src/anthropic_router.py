"""Anthropic Messages 协议的外部与管理台隔离路由。"""

import asyncio
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from config import get_available_models as get_configured_models
from .anthropic_compat import (
    SUPPORTED_ANTHROPIC_VERSION,
    AnthropicProtocolError,
    synthetic_anthropic_model_id,
    translate_anthropic_request,
)
from .anthropic_errors import AnthropicAPIError, get_anthropic_request_id
from .anthropic_response import (
    AnthropicDownstreamAdapter,
    AnthropicResponseContext,
    map_anthropic_error,
)
from .api_key_store import api_key_store
from .auth_router import require_session_user
from .auth_types import AuthenticatedUser
from .chat_execution import CodeBuddyCredentialError, execute_codebuddy_chat
from .models_manager import models_manager
from .private_response import PrivateNoStoreRoute
from .request_processor import PreparedCodeBuddyRequest, RequestProcessor
from .stream_service import UpstreamAPIError
from .usage_stats_context import UsageStatsContext, create_usage_stats_context
from .users_store import users_store

logger = logging.getLogger(__name__)
ANTHROPIC_MODEL_DISCOVERY_TIMEOUT_SECONDS = 2.5


anthropic_api_key = APIKeyHeader(
    name="x-api-key",
    scheme_name="AnthropicApiKey",
    auto_error=False,
)
anthropic_bearer = HTTPBearer(
    scheme_name="AnthropicBearer",
    bearerFormat="sk-...",
    auto_error=False,
)


ANTHROPIC_MESSAGES_OPENAPI_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["model", "max_tokens", "messages"],
                "additionalProperties": True,
                "properties": {
                    "model": {"type": "string", "minLength": 1},
                    "max_tokens": {"type": "integer", "minimum": 0},
                    "messages": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["role", "content"],
                            "additionalProperties": True,
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "enum": ["user", "assistant", "system"],
                                },
                                "content": {},
                            },
                        },
                    },
                    "system": {},
                    "stream": {"type": "boolean", "default": False},
                    "temperature": {"type": "number"},
                    "top_p": {"type": "number"},
                    "stop_sequences": {"type": "array", "items": {"type": "string"}},
                    "tools": {"type": "array", "items": {"type": "object"}},
                    "tool_choice": {"type": "object"},
                    "thinking": {"type": "object"},
                    "metadata": {"type": "object"},
                    "output_config": {},
                },
            }
        }
    },
}

ANTHROPIC_COUNT_TOKENS_NOT_FOUND_OPENAPI_RESPONSE = {
    "description": "Token counting is not supported",
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "required": ["type", "error", "request_id"],
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string", "enum": ["error"]},
                    "error": {
                        "type": "object",
                        "required": ["type", "message"],
                        "additionalProperties": False,
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["not_found_error"],
                            },
                            "message": {"type": "string"},
                        },
                    },
                    "request_id": {"type": "string"},
                },
            }
        }
    },
}


def _error(
        request: Request,
        status_code: int,
        error_type: str,
        message: str,
        *,
        headers: Optional[Dict[str, str]] = None,
) -> AnthropicAPIError:
    return AnthropicAPIError(
        status_code,
        error_type,
        message,
        get_anthropic_request_id(request),
        headers=headers,
    )


def require_anthropic_api_key_user(
        request: Request,
        _x_api_key: Optional[str] = Security(anthropic_api_key),
        _bearer: Optional[HTTPAuthorizationCredentials] = Security(anthropic_bearer),
) -> AuthenticatedUser:
    """严格解析 x-api-key/Bearer，并只验证最终确定的一个 Key。"""
    del _x_api_key, _bearer
    if not users_store.has_users_file():
        raise _error(request, 500, "api_error", "No authentication users are configured")

    header_key = request.headers.get("x-api-key")
    if header_key is not None:
        header_key = header_key.strip()
        if not header_key:
            raise _error(request, 401, "authentication_error", "Invalid authentication credentials")

    authorization = request.headers.get("Authorization")
    bearer_key: Optional[str] = None
    if authorization is not None:
        parts = authorization.strip().split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise _error(request, 401, "authentication_error", "Invalid authentication credentials")
        bearer_key = parts[1]

    if header_key is None and bearer_key is None:
        raise _error(request, 401, "authentication_error", "Invalid authentication credentials")
    if header_key is not None and bearer_key is not None and header_key != bearer_key:
        raise _error(request, 401, "authentication_error", "Authentication headers do not match")

    selected_key = header_key if header_key is not None else bearer_key
    try:
        user = api_key_store.verify(selected_key or "")
    except Exception as error:
        logger.error("Anthropic API Key 存储验证失败: %s", type(error).__name__)
        raise _error(request, 500, "api_error", "API key verification failed") from error
    if user is None:
        raise _error(request, 401, "authentication_error", "Invalid authentication credentials")
    return user


def require_anthropic_session_user(request: Request) -> AuthenticatedUser:
    try:
        return require_session_user(request)
    except Exception as error:
        status_code = getattr(error, "status_code", 500)
        if status_code == 401:
            raise _error(
                request,
                401,
                "authentication_error",
                "Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
        raise _error(request, 500, "api_error", "Session authentication failed") from error


def require_anthropic_version(
        request: Request,
        anthropic_version: str = Header(..., alias="anthropic-version"),
        anthropic_beta: str = Header("", alias="anthropic-beta"),
) -> None:
    if anthropic_version != SUPPORTED_ANTHROPIC_VERSION:
        raise _error(
            request,
            400,
            "invalid_request_error",
            f"anthropic-version must be {SUPPORTED_ANTHROPIC_VERSION}",
        )
    del anthropic_beta


def upstream_error_as_anthropic(request: Request, error: UpstreamAPIError) -> AnthropicAPIError:
    status_code, error_type, message = map_anthropic_error(error)
    retry_after = error.safe_headers.get("Retry-After")
    headers = {"Retry-After": retry_after} if retry_after else None
    return AnthropicAPIError(
        status_code,
        error_type,
        message,
        get_anthropic_request_id(request),
        headers=headers,
    )


async def _available_models(user: AuthenticatedUser) -> List[str]:
    try:
        return await asyncio.wait_for(
            models_manager.get_available_models(user),
            timeout=ANTHROPIC_MODEL_DISCOVERY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning("Anthropic 模型发现超过 2.5 秒，使用当前用户配置模型")
        return get_configured_models(user)


async def anthropic_messages(
        request: Request,
        user: AuthenticatedUser,
        stats_context: Optional[UsageStatsContext] = None,
        request_bytes: int = 0,
) -> Any:
    """校验、转换并执行一个 Anthropic Messages 请求。"""
    request_id = get_anthropic_request_id(request)
    if stats_context is not None:
        stats_context.capture_request_bytes(request_bytes)
    try:
        try:
            request_body = await request.json()
        except Exception as error:
            if stats_context is not None:
                stats_context.mark_failure("validation_error", 400)
            raise _error(request, 400, "invalid_request_error", "Invalid JSON request body") from error

        if stats_context is not None and isinstance(request_body, dict):
            stats_context.capture_request_shape(request_body)
        try:
            converted = translate_anthropic_request(request_body)
        except AnthropicProtocolError as error:
            if stats_context is not None:
                stats_context.mark_failure("validation_error", 400)
            raise _error(request, 400, "invalid_request_error", str(error)) from error

        prepared_base = RequestProcessor.prepare_request(converted, user)
        response_model = str(request_body["model"])
        prepared = PreparedCodeBuddyRequest(
            payload=prepared_base.payload,
            client_wants_stream=prepared_base.client_wants_stream,
            response_model=response_model,
        )
        adapter = AnthropicDownstreamAdapter(AnthropicResponseContext(
            message_id=f"msg_{uuid.uuid4().hex}",
            request_id=request_id,
            model=response_model,
        ))
        try:
            result = await execute_codebuddy_chat(
                prepared,
                user,
                stats_context=stats_context,
                response_adapter=adapter,
            )
        except CodeBuddyCredentialError as error:
            if stats_context is not None:
                stats_context.mark_failure("no_credential", 500)
            raise _error(request, 500, "api_error", str(error)) from error
        except UpstreamAPIError as error:
            raise upstream_error_as_anthropic(request, error) from error

        if prepared.client_wants_stream:
            return result
        return JSONResponse(result, headers={"request-id": request_id})
    except AnthropicAPIError:
        raise
    except Exception as error:
        logger.error("Anthropic Messages 请求执行失败: %s", type(error).__name__)
        if stats_context is not None:
            stats_context.mark_failure("internal_error", 500)
        raise _error(request, 500, "api_error", "Internal server error") from error


def create_anthropic_router(
        auth_dependency: Callable[..., AuthenticatedUser],
        route_name_prefix: str,
        stats_source: str,
        *,
        include_in_schema: bool,
) -> APIRouter:
    router = APIRouter(route_class=PrivateNoStoreRoute)

    @router.post(
        "/v1/messages",
        name=f"{route_name_prefix}_messages",
        include_in_schema=include_in_schema,
        openapi_extra={"requestBody": ANTHROPIC_MESSAGES_OPENAPI_REQUEST_BODY},
    )
    async def messages_route(
            request: Request,
            _version: None = Depends(require_anthropic_version),
            user: AuthenticatedUser = Depends(auth_dependency),
    ):
        del _version
        stats_context = create_usage_stats_context(request, user, stats_source)
        request_bytes = len(await request.body())
        return await anthropic_messages(request, user, stats_context, request_bytes)

    @router.get(
        "/v1/models",
        name=f"{route_name_prefix}_models",
        include_in_schema=include_in_schema,
    )
    async def models_route(
            request: Request,
            _version: None = Depends(require_anthropic_version),
            user: AuthenticatedUser = Depends(auth_dependency),
    ):
        del _version
        request_id = get_anthropic_request_id(request)
        try:
            models = await _available_models(user)
        except Exception as error:
            raise _error(request, 500, "api_error", "Unable to load models") from error
        data = [
            {"id": synthetic_anthropic_model_id(model), "display_name": model}
            for model in models
        ]
        return JSONResponse({
            "data": data,
            "has_more": False,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None,
        }, headers={"request-id": request_id})

    @router.post(
        "/v1/messages/count_tokens",
        name=f"{route_name_prefix}_count_tokens",
        include_in_schema=include_in_schema,
        status_code=404,
        response_model=None,
        responses={404: ANTHROPIC_COUNT_TOKENS_NOT_FOUND_OPENAPI_RESPONSE},
    )
    async def count_tokens_route(
            request: Request,
            _version: None = Depends(require_anthropic_version),
            _user: AuthenticatedUser = Depends(auth_dependency),
    ):
        del _version, _user
        raise _error(
            request,
            404,
            "not_found_error",
            "Token counting is not supported by this CodeBuddy gateway",
        )

    return router


external_anthropic_router = create_anthropic_router(
    require_anthropic_api_key_user,
    "external_anthropic",
    "external_api",
    include_in_schema=True,
)
playground_anthropic_router = create_anthropic_router(
    require_anthropic_session_user,
    "playground_anthropic",
    "admin_playground",
    include_in_schema=False,
)
