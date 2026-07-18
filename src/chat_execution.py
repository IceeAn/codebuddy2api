"""前端协议共享的 CodeBuddy 凭证选择、请求头生成和上游执行。"""

from typing import Any, Callable, Dict, Optional

from .auth_types import AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import get_token_manager_for_user
from .request_processor import PreparedCodeBuddyRequest
from .stream_service import CodeBuddyStreamService
from .usage_stats_context import UsageStatsContext


class CodeBuddyCredentialError(RuntimeError):
    pass


def select_codebuddy_credential(token_manager: Any) -> Dict[str, Any]:
    try:
        credential = token_manager.get_next_credential()
    except Exception as error:
        raise CodeBuddyCredentialError("CodeBuddy credential selection failed") from error
    if not isinstance(credential, dict) or not credential.get("bearer_token"):
        raise CodeBuddyCredentialError("No valid CodeBuddy credential is available")
    return credential


def select_codebuddy_credential_with_id(token_manager: Any) -> tuple[Optional[str], Dict[str, Any]]:
    """原子选择凭证，并兼容不提供稳定选择接口的测试替身。"""
    selector_method = getattr(type(token_manager), "select_next_credential", None)
    selector = getattr(token_manager, "select_next_credential", None)
    if callable(selector_method) or callable(selector):
        try:
            selected = token_manager.select_next_credential()
        except Exception as error:
            raise CodeBuddyCredentialError("CodeBuddy credential selection failed") from error
        if (
                isinstance(selected, tuple)
                and len(selected) == 2
                and isinstance(selected[0], str)
                and isinstance(selected[1], dict)
                and selected[1].get("bearer_token")
        ):
            return selected
        if callable(selector_method):
            raise CodeBuddyCredentialError("No valid CodeBuddy credential is available")

    credential = select_codebuddy_credential(token_manager)
    current_info = token_manager.get_current_credential_info()
    credential_id = current_info.get("credential_id") if isinstance(current_info, dict) else None
    return credential_id, credential


async def execute_codebuddy_chat(
        prepared_request: PreparedCodeBuddyRequest,
        user: AuthenticatedUser,
        *,
        stats_context: Optional[UsageStatsContext] = None,
        response_adapter: Optional[Any] = None,
        conversation_headers: Optional[Dict[str, Optional[str]]] = None,
        token_manager_factory: Callable[[AuthenticatedUser], Any] = get_token_manager_for_user,
        credential_selector: Callable[[Any], Any] = select_codebuddy_credential_with_id,
        header_generator: Callable[..., Dict[str, str]] = codebuddy_api_client.generate_codebuddy_headers,
        service_factory: Callable[..., CodeBuddyStreamService] = CodeBuddyStreamService,
) -> Any:
    """执行已经完成协议转换和产品策略处理的聊天请求。"""
    token_manager = token_manager_factory(user)
    selected = credential_selector(token_manager)
    if isinstance(selected, tuple) and len(selected) == 2:
        credential_id, credential = selected
    else:
        credential = selected
        credential_id = None
    if stats_context is not None:
        credential_info = None
        info_getter = getattr(token_manager, "get_credential_info_by_id", None)
        if credential_id is not None and callable(info_getter):
            candidate = info_getter(credential_id)
            if isinstance(candidate, dict):
                credential_info = candidate
        if credential_info is None:
            current_info = token_manager.get_current_credential_info()
            if credential_id is None:
                credential_id = current_info.get("credential_id")
                credential_info = current_info
            elif current_info.get("credential_id") == credential_id:
                credential_info = current_info
        current_info = credential_info or {}
        credential_label = (
            current_info.get("filename")
            or current_info.get("user_id")
            or credential_id
        )
        stats_context.capture_credential(credential_id, credential_label)

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
    if stats_context is not None:
        stats_context.capture_prepared_request(prepared_request.payload)
    service = service_factory(observer=stats_context)
    kwargs = {
        "response_model": prepared_request.response_model,
    }
    if response_adapter is not None:
        kwargs["response_adapter"] = response_adapter
    if prepared_request.client_wants_stream:
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
