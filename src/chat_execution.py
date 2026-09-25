"""前端协议共享的 CodeBuddy 凭证选择、请求头生成和上游执行。"""

import logging
from typing import Any, Callable, Dict, Optional

from .auth_types import AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import CodeBuddyTokenManager, get_token_manager_for_user
from .request_processor import PreparedCodeBuddyRequest
from .stream_service import CodeBuddyStreamService
from .usage_stats_context import UsageStatsContext

logger = logging.getLogger(__name__)


class CodeBuddyCredentialError(RuntimeError):
    pass


def select_codebuddy_credential_with_id(
        token_manager: CodeBuddyTokenManager,
) -> tuple[str, Dict[str, Any], int]:
    """通过令牌管理器的原子接口选择带稳定 ID 的凭证。"""
    try:
        selected = token_manager.select_next_credential()
    except Exception as error:
        raise CodeBuddyCredentialError("CodeBuddy credential selection failed") from error
    if (
            isinstance(selected, tuple)
            and len(selected) == 3
            and isinstance(selected[0], str)
            and bool(selected[0])
            and isinstance(selected[1], dict)
            and selected[1].get("bearer_token")
    ):
        return selected
    raise CodeBuddyCredentialError("No valid CodeBuddy credential is available")


def check_codebuddy_credential_availability(
        user: AuthenticatedUser,
        *,
        token_manager_factory: Callable[[AuthenticatedUser], CodeBuddyTokenManager] = get_token_manager_for_user,
) -> Optional[bool]:
    """读取凭证快照；检查失败时返回未知，避免覆盖原始请求准备异常。"""
    try:
        return bool(token_manager_factory(user).has_usable_credential())
    except Exception as error:
        logger.warning("请求准备失败后的凭证检查也失败: %s", type(error).__name__)
        return None


async def execute_codebuddy_chat(
        prepared_request: PreparedCodeBuddyRequest,
        user: AuthenticatedUser,
        *,
        stats_context: UsageStatsContext,
        response_adapter: Optional[Any] = None,
        conversation_headers: Optional[Dict[str, Optional[str]]] = None,
        token_manager_factory: Callable[[AuthenticatedUser], CodeBuddyTokenManager] = get_token_manager_for_user,
        credential_selector: Callable[[CodeBuddyTokenManager], tuple[str, Dict[str, Any], int]] = select_codebuddy_credential_with_id,
        header_generator: Callable[..., Dict[str, str]] = codebuddy_api_client.generate_codebuddy_headers,
        service_factory: Callable[..., CodeBuddyStreamService] = CodeBuddyStreamService,
) -> Any:
    """执行已经完成协议转换和产品策略处理的聊天请求。"""
    token_manager = token_manager_factory(user)
    credential_id, credential, credential_generation = credential_selector(token_manager)
    credential_info = token_manager.get_credential_info_by_id(credential_id)
    if credential_info is None:
        current_info = token_manager.get_current_credential_info()
        if current_info.get("credential_id") == credential_id:
            credential_info = current_info
    current_info = credential_info or {}
    credential_label = (
        current_info.get("filename")
        or current_info.get("user_id")
        or credential_id
    )
    stats_context.capture_credential(
        credential_id,
        credential_label,
        generation=credential_generation,
    )

    extra = conversation_headers or {}
    headers = header_generator(
        bearer_token=credential.get("bearer_token"),
        user_id=credential.get("user_id"),
        account_uid=credential.get("account_uid"),
        domain=credential.get("domain"),
        enterprise_id=credential.get("enterprise_id"),
        department_full_name=credential.get("department_full_name"),
        conversation_id=extra.get("conversation_id"),
        conversation_request_id=extra.get("conversation_request_id"),
        conversation_message_id=extra.get("conversation_message_id"),
        request_id=extra.get("request_id"),
    )
    stats_context.capture_prepared_request(
        prepared_request.payload, model_is_configured=prepared_request.model_is_configured,
    )
    service = service_factory(observer=stats_context)
    kwargs = {
        "response_model": prepared_request.response_model,
    }
    if response_adapter is not None:
        kwargs["response_adapter"] = response_adapter
    if prepared_request.client_wants_stream:
        if response_adapter is None:
            kwargs["include_usage"] = prepared_request.client_include_usage
        return await service.handle_stream_response(
            prepared_request.payload,
            headers,
            **kwargs,
        )
    return await service.handle_non_stream_response(
        prepared_request.payload,
        headers,
        **kwargs,
    )
