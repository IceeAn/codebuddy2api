"""CodeBuddy OAuth 启动、轮询和 token 保存逻辑。"""
import base64
import inspect
import json
import logging
import secrets
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlsplit

import httpx

from config import get_codebuddy_api_endpoint, get_codebuddy_api_host
from .auth_types import AuthenticatedUser
from .credential_store import build_user_credential_filename
from .stream_service import get_http_client

logger = logging.getLogger(__name__)
AUTH_START_FAILED_MESSAGE = "认证启动失败，请稍后重试"

AUTH_STATE_TTL_SECONDS = 600
AUTH_MAX_ACTIVE_PER_USER = 3
AUTH_MAX_START_ATTEMPTS = 5
AUTH_START_WINDOW_SECONDS = 60

AUTH_ERROR_DETAILS = {
    12005: ("license_seat_limit", "企业许可证没有可用席位"),
    11212: ("license_expired", "CodeBuddy 许可证已过期"),
    11216: ("trial_expired", "CodeBuddy 试用授权已过期"),
    10081: ("ip_restricted", "当前 IP 被 CodeBuddy 访问策略限制"),
}


class AuthStartLimitError(RuntimeError):
    """OAuth 启动并发或频率达到上限。"""

    def __init__(self, retry_after: int):
        super().__init__("OAuth start limit exceeded")
        self.retry_after = max(1, int(retry_after))


def is_safe_external_auth_url(value: Any) -> bool:
    """仅接受无用户信息、无控制字符的绝对 HTTP(S) 认证 URL。"""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() in {"http", "https"}
        and bool(hostname)
        and parsed.username is None
    )


def get_auth_start_headers() -> Dict[str, str]:
    """生成启动认证 /state 所需的请求头。"""
    request_id = str(uuid.uuid4()).replace("-", "")
    codebuddy_host = get_codebuddy_api_host()
    return {
        "Host": codebuddy_host,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "X-Requested-With": "XMLHttpRequest",
        "X-Domain": codebuddy_host,
        "X-No-Authorization": "true",
        "X-No-User-Id": "true",
        "X-No-Enterprise-Id": "true",
        "X-No-Department-Info": "true",
        "User-Agent": "CLI/2.107.0 CodeBuddy/2.107.0",
        "X-Product": "SaaS",
        "X-Request-ID": request_id,
    }


def get_auth_poll_headers() -> Dict[str, str]:
    """生成轮询认证 /token 所需的请求头。"""
    request_id = str(uuid.uuid4()).replace("-", "")
    span_id = secrets.token_hex(8)
    codebuddy_host = get_codebuddy_api_host()
    return {
        "Host": codebuddy_host,
        "Accept": "application/json, text/plain, */*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "X-Requested-With": "XMLHttpRequest",
        "X-Request-ID": request_id,
        "b3": f"{request_id}-{span_id}-1-",
        "X-B3-TraceId": request_id,
        "X-B3-ParentSpanId": "",
        "X-B3-SpanId": span_id,
        "X-B3-Sampled": "1",
        "X-No-Authorization": "true",
        "X-No-User-Id": "true",
        "X-No-Enterprise-Id": "true",
        "X-No-Department-Info": "true",
        "X-Domain": codebuddy_host,
        "User-Agent": "CLI/2.107.0 CodeBuddy/2.107.0",
        "X-Product": "SaaS",
    }


def get_codebuddy_auth_state_endpoint() -> str:
    return f"{get_codebuddy_api_endpoint()}/v2/plugin/auth/state"


def get_codebuddy_auth_token_endpoint() -> str:
    return f"{get_codebuddy_api_endpoint()}/v2/plugin/auth/token"


def get_codebuddy_login_account_endpoint() -> str:
    return f"{get_codebuddy_api_endpoint()}/v2/plugin/login/account"


def get_codebuddy_accounts_endpoint() -> str:
    return f"{get_codebuddy_api_endpoint()}/v2/plugin/accounts"


class AuthStateStore:
    """记录 auth_state 的所属用户，防止跨用户轮询截取 token。"""

    def __init__(
            self,
            ttl_seconds: int = AUTH_STATE_TTL_SECONDS,
            max_active_per_user: int = AUTH_MAX_ACTIVE_PER_USER,
            max_start_attempts: int = AUTH_MAX_START_ATTEMPTS,
            start_window_seconds: int = AUTH_START_WINDOW_SECONDS,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_active_per_user = max_active_per_user
        self.max_start_attempts = max_start_attempts
        self.start_window_seconds = start_window_seconds
        self._owners: Dict[str, Dict[str, Any]] = {}
        self._starting: Dict[str, Dict[str, Any]] = {}
        self._start_attempts: Dict[str, list[int]] = {}

    def begin_start(self, user: AuthenticatedUser) -> str:
        """登记一次启动尝试，并为上游请求预留活动名额。"""
        current_time = int(time.time())
        self.cleanup_expired(current_time)
        attempts = self._start_attempts.setdefault(user.username, [])
        if len(attempts) >= self.max_start_attempts:
            retry_after = attempts[0] + self.start_window_seconds - current_time
            raise AuthStartLimitError(retry_after)
        attempts.append(current_time)

        active_created_at = [
            int(state_info.get("created_at", current_time))
            for state_info in self._owners.values()
            if state_info.get("username") == user.username
            and state_info.get("consumed_at") is None
        ]
        active_created_at.extend(
            int(start_info.get("created_at", current_time))
            for start_info in self._starting.values()
            if start_info.get("username") == user.username
        )
        if len(active_created_at) >= self.max_active_per_user:
            retry_after = min(active_created_at) + self.ttl_seconds - current_time
            raise AuthStartLimitError(retry_after)

        reservation = secrets.token_urlsafe(18)
        self._starting[reservation] = {
            "username": user.username,
            "created_at": current_time,
        }
        return reservation

    def finish_start(
            self,
            reservation: str,
            auth_state: str,
            user: AuthenticatedUser,
    ) -> bool:
        """将启动预留原子转换为归属于当前用户的 auth_state。"""
        self.cleanup_expired()
        start_info = self._starting.pop(reservation, None)
        if not start_info or start_info.get("username") != user.username:
            return False
        if auth_state in self._owners:
            return False
        self._owners[auth_state] = {
            "username": user.username,
            "created_at": int(time.time()),
            "consumed_at": None,
            "progress": None,
        }
        return True

    def cancel_start(self, reservation: str) -> None:
        self._starting.pop(reservation, None)

    def validate_owner(self, auth_state: str, user: AuthenticatedUser) -> bool:
        self.cleanup_expired()
        state_info = self._owners.get(auth_state)
        if not state_info:
            return False
        return state_info.get("username") == user.username and state_info.get("consumed_at") is None

    def consume(self, auth_state: str, user: AuthenticatedUser) -> bool:
        """将所属用户的待处理 state 标记为已消费，并保留墓碑防止重放。"""
        self.cleanup_expired()
        state_info = self._owners.get(auth_state)
        if not state_info:
            return False
        if state_info.get("username") != user.username or state_info.get("consumed_at") is not None:
            return False
        state_info["progress"] = None
        state_info["consumed_at"] = int(time.time())
        return True

    def get_progress(self, auth_state: str, user: AuthenticatedUser) -> Optional[Dict[str, Any]]:
        """读取当前用户 OAuth state 的服务端暂存进度。"""
        self.cleanup_expired()
        state_info = self._owners.get(auth_state)
        if not state_info or state_info.get("username") != user.username:
            return None
        if state_info.get("consumed_at") is not None:
            return None
        progress = state_info.get("progress")
        return progress.copy() if isinstance(progress, dict) else None

    def set_progress(
            self,
            auth_state: str,
            user: AuthenticatedUser,
            progress: Dict[str, Any],
    ) -> bool:
        """仅为所属用户保存尚未完成的敏感 OAuth 进度。"""
        self.cleanup_expired()
        state_info = self._owners.get(auth_state)
        if not state_info or state_info.get("username") != user.username:
            return False
        if state_info.get("consumed_at") is not None:
            return False
        state_info["progress"] = progress.copy()
        return True

    def cleanup_expired(self, current_time: Optional[int] = None):
        if current_time is None:
            current_time = int(time.time())
        expired_states = [
            state
            for state, state_info in self._owners.items()
            if current_time - int(
                state_info.get("consumed_at")
                if state_info.get("consumed_at") is not None
                else state_info.get("created_at", 0)
            ) > self.ttl_seconds
        ]
        for state in expired_states:
            self._owners.pop(state, None)
        expired_reservations = [
            reservation
            for reservation, start_info in self._starting.items()
            if current_time - int(start_info.get("created_at", 0)) > self.ttl_seconds
        ]
        for reservation in expired_reservations:
            self._starting.pop(reservation, None)
        for username, attempts in list(self._start_attempts.items()):
            fresh_attempts = [
                attempt
                for attempt in attempts
                if current_time - attempt < self.start_window_seconds
            ]
            if fresh_attempts:
                self._start_attempts[username] = fresh_attempts
            else:
                self._start_attempts.pop(username, None)


auth_state_store = AuthStateStore()


class CodeBuddyAuthClient:
    """CodeBuddy OAuth 上游客户端。"""

    def __init__(self, http_client_factory=None):
        self._http_client_factory = http_client_factory or get_http_client

    async def _get_http_client(self):
        client = self._http_client_factory()
        if inspect.isawaitable(client):
            return await client
        return client

    async def start_auth(self) -> Dict[str, Any]:
        """启动 CodeBuddy 认证流程。"""
        try:
            logger.info("启动CodeBuddy认证流程...")
            headers = get_auth_start_headers()

            client = await self._get_http_client()
            result = await self._request_state(client, headers)
            auth_state = result.get("auth_state")
            auth_url = result.get("auth_url")

            if auth_state and auth_url:
                token_endpoint = f"{get_codebuddy_auth_token_endpoint()}?state={auth_state}"

                return {
                    "success": True,
                    "method": "codebuddy_real_auth",
                    "auth_state": auth_state,
                    "verification_uri_complete": auth_url,
                    "verification_uri": get_codebuddy_api_endpoint(),
                    "token_endpoint": token_endpoint,
                    "expires_in": AUTH_STATE_TTL_SECONDS,
                    "interval": 5,
                    "status": "awaiting_login",
                    "instructions": "请点击链接完成CodeBuddy登录",
                    "message": "请使用提供的链接登录CodeBuddy",
                    "platform": "CLI",
                }

            return {
                "success": False,
                "error": "auth_start_failed",
                "message": "无法启动认证流程",
            }

        except Exception as error:
            logger.exception("启动CodeBuddy认证失败（%s）", type(error).__name__)
            return {
                "success": False,
                "error": "auth_start_failed",
                "message": AUTH_START_FAILED_MESSAGE,
            }

    async def _request_state(self, client: httpx.AsyncClient, headers: Dict[str, str]) -> Dict[str, Optional[str]]:
        state_url = f"{get_codebuddy_auth_state_endpoint()}?platform=CLI"
        response = await client.post(state_url, json={}, headers=headers, timeout=30)
        if response.status_code != 200:
            return {"auth_state": None, "auth_url": None}

        result = response.json()
        if not isinstance(result, dict):
            return {"auth_state": None, "auth_url": None}

        if result.get("code") != 0 or not result.get("data"):
            return {"auth_state": None, "auth_url": None}

        data = result["data"]
        if not isinstance(data, dict):
            return {"auth_state": None, "auth_url": None}

        auth_url = data.get("authUrl")
        if not is_safe_external_auth_url(auth_url):
            return {"auth_state": None, "auth_url": None}

        return {"auth_state": data.get("state"), "auth_url": auth_url}

    @staticmethod
    def _business_error(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        code = result.get("code")
        detail = AUTH_ERROR_DETAILS.get(code)
        if not detail:
            return None
        error, message = detail
        return {
            "status": "error",
            "error": error,
            "message": message,
            "code": code,
            "http_status": 403,
        }

    @staticmethod
    def _auth_headers(token_data: Dict[str, Any]) -> Dict[str, str]:
        headers = get_auth_poll_headers()
        for header in (
            "X-No-Authorization",
            "X-No-User-Id",
            "X-No-Enterprise-Id",
            "X-No-Department-Info",
        ):
            headers.pop(header, None)
        headers["Authorization"] = f"Bearer {token_data['access_token']}"
        domain = token_data.get("domain")
        if isinstance(domain, str) and domain:
            headers["X-Domain"] = domain
        return headers

    @staticmethod
    def _token_data(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("accessToken"), str):
            return None
        token_data = {
            "access_token": data["accessToken"],
            "bearer_token": data["accessToken"],
            "token_type": data.get("tokenType", "Bearer"),
            "expires_in": data.get("expiresIn"),
            "expires_at": data.get("expiresAt"),
            "refresh_token": data.get("refreshToken"),
            "refresh_expires_in": data.get("refreshExpiresIn"),
            "refresh_expires_at": data.get("refreshExpiresAt"),
            "session_state": data.get("sessionState"),
            "scope": data.get("scope"),
            "domain": data.get("domain"),
            "enterprise_id": data.get("enterprise_id") or data.get("enterpriseId"),
            "upstream_responses": {"login_token": result},
        }
        return {key: value for key, value in token_data.items() if value is not None}

    async def poll_status(
            self,
            auth_state: str,
            progress: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """依次轮询 Token、当前账号和可用账号列表。"""
        token_data = progress.copy() if isinstance(progress, dict) else None
        try:
            client = await self._get_http_client()
            if token_data is None:
                response = await client.get(
                    f"{get_codebuddy_auth_token_endpoint()}?state={auth_state}",
                    headers=get_auth_poll_headers(),
                    timeout=30,
                )
                if response.status_code != 200:
                    return {"status": "error", "error": "auth_unavailable", "message": "认证服务暂时不可用", "http_status": 503}
                result = response.json()
                if not isinstance(result, dict):
                    return {"status": "error", "error": "invalid_auth_response", "message": "认证服务返回无效响应", "http_status": 502}
                business_error = self._business_error(result)
                if business_error:
                    return business_error
                if result.get("code") == 11217:
                    return {"status": "pending", "stage": "token", "message": "等待用户完成登录", "code": 11217}
                if result.get("code") != 0:
                    return {"status": "error", "error": "invalid_auth_response", "message": "认证服务返回未知状态", "code": result.get("code"), "http_status": 502}
                token_data = self._token_data(result)
                if token_data is None:
                    return {"status": "error", "error": "invalid_auth_response", "message": "认证响应缺少访问令牌", "http_status": 502}

            headers = self._auth_headers(token_data)
            account_response = await client.get(
                f"{get_codebuddy_login_account_endpoint()}?state={auth_state}",
                headers=headers,
                timeout=30,
            )
            if account_response.status_code != 200:
                return {"status": "error", "error": "auth_unavailable", "message": "账号服务暂时不可用", "http_status": 503, "progress": token_data}
            account_result = account_response.json()
            if not isinstance(account_result, dict):
                return {"status": "error", "error": "invalid_auth_response", "message": "账号服务返回无效响应", "http_status": 502}
            business_error = self._business_error(account_result)
            if business_error:
                return business_error
            if account_result.get("code") == 12151:
                return {"status": "pending", "stage": "account", "message": "等待账号信息准备完成", "code": 12151, "progress": token_data}
            account = account_result.get("data")
            if account_result.get("code") != 0 or not isinstance(account, dict) or not isinstance(account.get("uid"), str):
                return {"status": "error", "error": "invalid_auth_response", "message": "账号服务返回无效账号", "http_status": 502}

            accounts_response = await client.get(
                get_codebuddy_accounts_endpoint(),
                headers=headers,
                timeout=30,
            )
            if accounts_response.status_code != 200:
                return {"status": "pending", "stage": "accounts", "message": "等待账号列表准备完成", "progress": token_data}
            accounts_result = accounts_response.json()
            if not isinstance(accounts_result, dict):
                return {"status": "error", "error": "invalid_auth_response", "message": "账号列表返回无效响应", "http_status": 502}
            business_error = self._business_error(accounts_result)
            if business_error:
                return business_error
            accounts_data = accounts_result.get("data")
            accounts = accounts_data.get("accounts") if isinstance(accounts_data, dict) else None
            if accounts_result.get("code") != 0 or not isinstance(accounts, list):
                return {"status": "error", "error": "invalid_auth_response", "message": "账号列表返回无效数据", "http_status": 502}
            enabled_accounts = [item for item in accounts if isinstance(item, dict) and item.get("pluginEnabled") is True]
            upstream_responses = token_data.setdefault("upstream_responses", {})
            upstream_responses["login_account"] = account_result
            upstream_responses["accounts"] = accounts_result
            token_data["account"] = account
            token_data["accounts"] = enabled_accounts
            return {"status": "success", "message": "认证成功", "token_data": token_data}

        except Exception as error:
            logger.error("轮询认证状态失败（%s）", type(error).__name__)
            return {
                "status": "error",
                "error": "auth_unavailable",
                "message": "认证状态查询失败",
                "http_status": 503,
                **({"progress": token_data} if token_data else {}),
            }


class TokenParser:
    """从 CodeBuddy token 响应中提取凭证文件内容。"""

    @staticmethod
    def _normalize_epoch(value: Any) -> Optional[int]:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            return None
        normalized = int(value)
        if normalized >= 100_000_000_000:
            normalized //= 1000
        return normalized

    @staticmethod
    def _site_type(api_endpoint: str) -> str:
        if api_endpoint == "https://copilot.tencent.com":
            return "china"
        if api_endpoint == "https://www.codebuddy.ai":
            return "international"
        return "custom"

    @staticmethod
    def _account_id(account: Dict[str, Any]) -> str:
        import hashlib

        source = "\0".join((
            str(account.get("type") or ""),
            str(account.get("enterpriseId") or account.get("enterprise_id") or ""),
            str(account.get("uid") or ""),
        ))
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _normalize_account(account: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(account, dict):
            return None
        uid = account.get("uid")
        if not isinstance(uid, str) or not uid:
            return None
        normalized = {
            "account_id": TokenParser._account_id(account),
            "uid": uid,
            "type": account.get("type"),
            "nickname": account.get("nickname"),
            "enterprise_id": account.get("enterpriseId") or account.get("enterprise_id"),
            "enterprise_name": account.get("enterpriseName") or account.get("enterprise_name"),
            "department_full_name": account.get("departmentFullName") or account.get("department_full_name"),
            "idp": account.get("idp"),
            "last_login": account.get("lastLogin") if "lastLogin" in account else account.get("last_login"),
            "plugin_enabled": account.get("pluginEnabled") if "pluginEnabled" in account else account.get("plugin_enabled"),
        }
        return {key: value for key, value in normalized.items() if value is not None}

    @staticmethod
    def build_credential_data(token_data: Dict[str, Any]) -> Dict[str, Any]:
        provided_created_at = token_data.get("created_at")
        created_at = (
            int(provided_created_at)
            if isinstance(provided_created_at, (int, float))
            and not isinstance(provided_created_at, bool)
            and provided_created_at > 0
            else int(time.time())
        )
        bearer_token = token_data.get("access_token") or token_data.get("bearer_token")
        user_id, user_info = TokenParser._extract_user_info(bearer_token, token_data)
        domain = token_data.get("domain") or user_info.get("domain")
        enterprise_id = token_data.get("enterprise_id") or user_info.get("enterprise_id")
        account = TokenParser._normalize_account(token_data.get("account"))
        if account:
            if account.get("type") == "personal":
                enterprise_id = None
            else:
                enterprise_id = account.get("enterprise_id") or enterprise_id
        if domain:
            user_info["domain"] = domain
        if enterprise_id:
            user_info["enterprise_id"] = enterprise_id
        else:
            user_info.pop("enterprise_id", None)

        api_endpoint = get_codebuddy_api_endpoint()
        expires_in = token_data.get("expires_in")
        expires_at = TokenParser._normalize_epoch(token_data.get("expires_at"))
        if expires_at is None and isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
            expires_at = created_at + int(expires_in)
        refresh_expires_in = token_data.get("refresh_expires_in")
        refresh_expires_at = TokenParser._normalize_epoch(token_data.get("refresh_expires_at"))
        if refresh_expires_at is None and isinstance(refresh_expires_in, (int, float)) and not isinstance(refresh_expires_in, bool):
            refresh_expires_at = created_at + int(refresh_expires_in)
        accounts = []
        for item in token_data.get("accounts") or []:
            normalized_account = TokenParser._normalize_account(item)
            if normalized_account:
                accounts.append(normalized_account)
        upstream_responses = token_data.get("upstream_responses")
        compatibility_data = None
        if "full_response" in token_data:
            compatibility_data = {"legacy_full_response": token_data.get("full_response")}

        credential_data = {
            "credential_schema_version": 2,
            "bearer_token": bearer_token,
            "user_id": user_id,
            "created_at": created_at,
            "updated_at": token_data.get("updated_at", created_at),
            "expires_in": expires_in,
            "expires_at": expires_at,
            "refresh_token": token_data.get("refresh_token"),
            "refresh_expires_in": refresh_expires_in,
            "refresh_expires_at": refresh_expires_at,
            "last_refresh_at": token_data.get("last_refresh_at"),
            "token_type": token_data.get("token_type", "Bearer"),
            "scope": token_data.get("scope"),
            "domain": domain,
            "enterprise_id": enterprise_id,
            "session_state": token_data.get("session_state"),
            "user_info": user_info,
            "auth_source": "oauth" if account or upstream_responses else "manual",
            "auth_platform": "CLI" if account or upstream_responses else None,
            "auth_client_version": "2.107.0" if account or upstream_responses else None,
            "api_endpoint": api_endpoint,
            "site_type": TokenParser._site_type(api_endpoint),
            "account_uid": account.get("uid") if account else None,
            "account_id": account.get("account_id") if account else None,
            "account_type": account.get("type") if account else None,
            "nickname": account.get("nickname") if account else None,
            "enterprise_name": account.get("enterprise_name") if account else None,
            "department_full_name": account.get("department_full_name") if account else None,
            "idp": account.get("idp") if account else None,
            "accounts": accounts or None,
            "upstream_responses": upstream_responses,
            "compatibility_data": compatibility_data,
        }
        return {key: value for key, value in credential_data.items() if value is not None}

    @staticmethod
    def _extract_user_info(bearer_token: Optional[str], token_data: Dict[str, Any]):
        del token_data
        user_info = {}

        try:
            if bearer_token and "." in bearer_token:
                parts = bearer_token.split(".")
                payload_part = parts[1]
                missing_padding = len(payload_part) % 4
                if missing_padding:
                    payload_part += "=" * (4 - missing_padding)

                try:
                    payload = base64.urlsafe_b64decode(payload_part)
                    jwt_data = json.loads(payload.decode("utf-8"))
                    user_id = jwt_data.get("sub") or TokenParser._fallback_user_id(bearer_token)
                    issuer_info = TokenParser._extract_issuer_info(jwt_data.get("iss"))
                    nickname = jwt_data.get("nickname") or jwt_data.get("preferred_username")
                    user_info = {
                        "sub": jwt_data.get("sub"),
                        "email": jwt_data.get("email"),
                        "email_verified": jwt_data.get("email_verified"),
                        "preferred_username": jwt_data.get("preferred_username"),
                        "nickname": nickname,
                        "name": jwt_data.get("name"),
                        "given_name": jwt_data.get("given_name"),
                        "family_name": jwt_data.get("family_name"),
                        "picture": jwt_data.get("picture"),
                        "locale": jwt_data.get("locale"),
                        "iss": jwt_data.get("iss"),
                        "aud": jwt_data.get("aud"),
                        "azp": jwt_data.get("azp"),
                        "exp": jwt_data.get("exp"),
                        "iat": jwt_data.get("iat"),
                        "nbf": jwt_data.get("nbf"),
                        "auth_time": jwt_data.get("auth_time"),
                        "scope": jwt_data.get("scope"),
                        "sid": jwt_data.get("sid"),
                        "session_state": jwt_data.get("sid"),
                        **issuer_info,
                    }
                    user_info = {key: value for key, value in user_info.items() if value is not None}
                    logger.info("成功解析JWT")
                except (json.JSONDecodeError, UnicodeDecodeError) as decode_error:
                    logger.warning(f"JWT payload解码失败: {decode_error}")
                    user_id = TokenParser._fallback_user_id(bearer_token)
            else:
                logger.warning("Bearer token为空或格式无效")
                user_id = TokenParser._fallback_user_id(bearer_token)
        except Exception as e:
            logger.error(f"JWT解析过程发生异常: {e}")
            user_id = TokenParser._fallback_user_id(bearer_token)

        return user_id, user_info

    @staticmethod
    def _fallback_user_id(bearer_token: Optional[str]) -> str:
        if not bearer_token:
            return "anonymous"
        return f"anonymous_{bearer_token[-8:]}"

    @staticmethod
    def _extract_issuer_info(issuer: Optional[str]) -> Dict[str, str]:
        if not issuer:
            return {}

        parsed = urlparse(issuer)
        info = {}
        if parsed.hostname:
            info["domain"] = parsed.hostname

        last_path_part = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if last_path_part.startswith("sso-") and len(last_path_part) > len("sso-"):
            info["enterprise_id"] = last_path_part[len("sso-"):]

        return info


class CodeBuddyTokenSaver:
    """保存 CodeBuddy token 到当前系统用户的凭证目录。"""

    async def save(self, token_data: Dict[str, Any], owner_user: AuthenticatedUser) -> bool:
        try:
            from .codebuddy_token_manager import get_token_manager_for_user

            credential_data = TokenParser.build_credential_data(token_data)
            user_id = credential_data.get("user_id", "unknown")
            filename = build_user_credential_filename(user_id)

            token_manager = get_token_manager_for_user(owner_user)
            before_ids = {
                item["credential_id"] for item in token_manager.get_credentials_info()
            }
            success = token_manager.add_credential_with_data(
                credential_data=credential_data,
                filename=filename,
            )

            if success:
                logger.info(f"成功保存CodeBuddy token，用户: {user_id}，文件: {filename}")
                from .credential_quota import credential_quota_manager
                for item in token_manager.get_credentials_info():
                    credential_id = item["credential_id"]
                    if credential_id not in before_ids:
                        credential_quota_manager.schedule_probe_if_running(
                            owner_user.username,
                            token_manager,
                            credential_id,
                        )
                        break

            return success
        except Exception as e:
            logger.error(f"保存CodeBuddy token失败: {e}")
            return False


codebuddy_auth_client = CodeBuddyAuthClient()
codebuddy_token_saver = CodeBuddyTokenSaver()


def validate_auth_state_owner(auth_state: str, user: AuthenticatedUser) -> bool:
    return auth_state_store.validate_owner(auth_state, user)


def consume_auth_state(auth_state: str, user: AuthenticatedUser) -> bool:
    return auth_state_store.consume(auth_state, user)


async def start_codebuddy_auth() -> Dict[str, Any]:
    return await codebuddy_auth_client.start_auth()


async def poll_codebuddy_auth_status(
        auth_state: str,
        owner_user: Optional[AuthenticatedUser] = None,
) -> Dict[str, Any]:
    progress = auth_state_store.get_progress(auth_state, owner_user) if owner_user else None
    result = await codebuddy_auth_client.poll_status(auth_state, progress)
    next_progress = result.get("progress")
    if owner_user and isinstance(next_progress, dict):
        auth_state_store.set_progress(auth_state, owner_user, next_progress)
    return result


async def save_codebuddy_token(token_data: Dict[str, Any], owner_user: AuthenticatedUser) -> bool:
    return await codebuddy_token_saver.save(token_data, owner_user)
