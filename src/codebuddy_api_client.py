"""CodeBuddy API Client - 直接调用 CodeBuddy API。"""
import logging
import platform
import re
import secrets
import uuid
from typing import Dict, Optional

logger = logging.getLogger(__name__)

CODEBUDDY_CLI_VERSION = "2.107.0"
OPENAI_JS_PACKAGE_VERSION = "6.25.0"
NODE_RUNTIME_VERSION = "v24.11.1"


def _safe_domain_header(domain: str, fallback: str) -> str:
    value = str(domain or "").strip()
    if not value:
        return fallback
    if re.fullmatch(r"[A-Za-z0-9.-]+", value):
        return value
    logger.warning("Ignoring unsafe CodeBuddy domain header value")
    return fallback


def _stainless_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x64"
    return machine or "x64"


def _stainless_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "MacOS"
    if system == "linux":
        return "Linux"
    if system == "windows":
        return "Windows"
    return platform.system() or "Linux"


class CodeBuddyAPIClient:
    """CodeBuddy API客户端"""

    def __init__(self):
        from config import get_codebuddy_api_endpoint
        self.base_url = get_codebuddy_api_endpoint()
        self.api_endpoint = self.base_url  # 直接使用base_url，不需要plugin前缀

    def generate_codebuddy_headers(
            self,
            bearer_token: str,
            user_id: Optional[str] = None,
            domain: str = None,
            enterprise_id: Optional[str] = None,
            conversation_id: Optional[str] = None,
            conversation_request_id: Optional[str] = None,
            conversation_message_id: Optional[str] = None,
            request_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        生成CodeBuddy API所需的完整请求头。
        优先使用传入的会话ID，如果未提供则随机生成。
        """
        from config import get_codebuddy_api_host
        if not user_id:
            raise ValueError("CodeBuddy 凭证缺少 user_id")

        codebuddy_host = get_codebuddy_api_host()
        codebuddy_domain = _safe_domain_header(domain, codebuddy_host)
        headers = {
            'Host': codebuddy_host,
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'x-stainless-arch': _stainless_arch(),
            'x-stainless-lang': 'js',
            'x-stainless-os': _stainless_os(),
            'x-stainless-package-version': OPENAI_JS_PACKAGE_VERSION,
            'x-stainless-retry-count': '0',
            'x-stainless-runtime': 'node',
            'x-stainless-runtime-version': NODE_RUNTIME_VERSION,
            'X-Conversation-ID': conversation_id or str(uuid.uuid4()),
            'X-Conversation-Request-ID': conversation_request_id or secrets.token_hex(16),
            'X-Conversation-Message-ID': conversation_message_id or str(uuid.uuid4()).replace('-', ''),
            'X-Request-ID': request_id or str(uuid.uuid4()).replace('-', ''),
            'X-Agent-Intent': 'craft',
            'X-Agent-Purpose': 'conversation',
            'X-IDE-Type': 'CLI',
            'X-IDE-Name': 'CLI',
            'X-IDE-Version': CODEBUDDY_CLI_VERSION,
            'Authorization': f'Bearer {bearer_token}',
            'X-Domain': codebuddy_domain,
            'User-Agent': f'CLI/{CODEBUDDY_CLI_VERSION} CodeBuddy/{CODEBUDDY_CLI_VERSION}',
            'X-Private-Data': 'false',
            'X-CodeBuddy-Request': '1',
            'X-Product': 'SaaS',
            'X-User-Id': user_id
        }
        if enterprise_id:
            headers["X-Enterprise-Id"] = enterprise_id
            headers["X-Tenant-Id"] = enterprise_id
        return headers


# 全局客户端实例
codebuddy_api_client = CodeBuddyAPIClient()
