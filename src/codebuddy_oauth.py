"""CodeBuddy OAuth 启动、轮询和 token 保存逻辑。"""
import base64
import json
import logging
import secrets
import time
import uuid
from typing import Any, Dict, Optional

import httpx

from config import get_codebuddy_api_endpoint, get_codebuddy_api_host, get_ssl_verify
from .auth_types import AuthenticatedUser

logger = logging.getLogger(__name__)

AUTH_STATE_TTL_SECONDS = 1800


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
        "Connection": "close",
        "X-Requested-With": "XMLHttpRequest",
        "X-Domain": codebuddy_host,
        "X-No-Authorization": "true",
        "X-No-User-Id": "true",
        "X-No-Enterprise-Id": "true",
        "X-No-Department-Info": "true",
        "User-Agent": "CLI/1.0.8 CodeBuddy/1.0.8",
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
        "Connection": "close",
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
        "User-Agent": "CLI/1.0.8 CodeBuddy/1.0.8",
        "X-Product": "SaaS",
    }


def get_codebuddy_auth_state_endpoint() -> str:
    return f"{get_codebuddy_api_endpoint()}/v2/plugin/auth/state"


def get_codebuddy_auth_token_endpoint() -> str:
    return f"{get_codebuddy_api_endpoint()}/v2/plugin/auth/token"


class AuthStateStore:
    """记录 auth_state 的所属用户，防止跨用户轮询截取 token。"""

    def __init__(self, ttl_seconds: int = AUTH_STATE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._owners: Dict[str, Dict[str, Any]] = {}

    def remember(self, auth_state: str, user: AuthenticatedUser) -> bool:
        """登记未占用的 state；已存在时保持原所属用户不变。"""
        self.cleanup_expired()
        if auth_state in self._owners:
            return False
        self._owners[auth_state] = {
            "username": user.username,
            "created_at": int(time.time()),
            "consumed_at": None,
        }
        return True

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
        state_info["consumed_at"] = int(time.time())
        return True

    def cleanup_expired(self):
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


auth_state_store = AuthStateStore()


class CodeBuddyAuthClient:
    """CodeBuddy OAuth 上游客户端。"""

    async def start_auth(self) -> Dict[str, Any]:
        """启动 CodeBuddy 认证流程。"""
        try:
            logger.info("启动CodeBuddy认证流程...")
            headers = get_auth_start_headers()

            async with httpx.AsyncClient(verify=get_ssl_verify(), trust_env=False) as client:
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
                        "expires_in": 1800,
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

        except Exception as e:
            logger.error(f"启动CodeBuddy认证失败: {e}")
            return {
                "success": False,
                "error": "auth_start_failed",
                "message": f"认证启动失败: {str(e)}",
            }

    async def _request_state(self, client: httpx.AsyncClient, headers: Dict[str, str]) -> Dict[str, Optional[str]]:
        nonce = secrets.token_hex(8)
        state_url = f"{get_codebuddy_auth_state_endpoint()}?platform=CLI&nonce={nonce}"
        response = await client.post(state_url, json={"nonce": nonce}, headers=headers, timeout=30)
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

        return {
            "auth_state": data.get("state"),
            "auth_url": data.get("authUrl"),
        }

    async def poll_status(self, auth_state: str) -> Dict[str, Any]:
        """轮询 CodeBuddy 认证状态。"""
        try:
            headers = get_auth_poll_headers()
            url = f"{get_codebuddy_auth_token_endpoint()}?state={auth_state}"

            async with httpx.AsyncClient(verify=get_ssl_verify(), trust_env=False) as client:
                response = await client.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                return {
                    "status": "error",
                    "message": f"API请求失败，状态码: {response.status_code}",
                    "response_text": response.text,
                }

            result = response.json()
            if result.get("code") == 11217:
                return {
                    "status": "pending",
                    "message": result.get("msg", "login ing..."),
                    "code": result.get("code"),
                }

            if result.get("code") == 0 and result.get("data") and result.get("data", {}).get("accessToken"):
                data = result.get("data", {})
                return {
                    "status": "success",
                    "message": "认证成功！",
                    "token_data": {
                        "access_token": data.get("accessToken"),
                        "bearer_token": data.get("accessToken"),
                        "token_type": data.get("tokenType", "Bearer"),
                        "expires_in": data.get("expiresIn"),
                        "refresh_token": data.get("refreshToken"),
                        "session_state": data.get("sessionState"),
                        "scope": data.get("scope"),
                        "domain": data.get("domain"),
                        "full_response": result,
                    },
                }

            return {
                "status": "unknown",
                "message": result.get("msg", "Unknown status"),
                "code": result.get("code"),
                "response": result,
            }

        except Exception as e:
            logger.error(f"轮询认证状态失败: {e}")
            return {
                "status": "error",
                "message": f"轮询失败: {str(e)}",
            }


class TokenParser:
    """从 CodeBuddy token 响应中提取凭证文件内容。"""

    @staticmethod
    def build_credential_data(token_data: Dict[str, Any]) -> Dict[str, Any]:
        token_data["created_at"] = int(time.time())
        bearer_token = token_data.get("access_token") or token_data.get("bearer_token")
        user_id, user_info = TokenParser._extract_user_info(bearer_token, token_data)

        credential_data = {
            "bearer_token": bearer_token,
            "user_id": user_id,
            "created_at": int(time.time()),
            "expires_in": token_data.get("expires_in"),
            "refresh_token": token_data.get("refresh_token"),
            "token_type": token_data.get("token_type", "Bearer"),
            "scope": token_data.get("scope"),
            "domain": token_data.get("domain"),
            "session_state": token_data.get("session_state"),
            "user_info": user_info,
            "full_response": token_data,
        }
        return {key: value for key, value in credential_data.items() if value is not None}

    @staticmethod
    def _extract_user_info(bearer_token: Optional[str], token_data: Dict[str, Any]):
        fallback_user_id = token_data.get("domain", "unknown")
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
                    user_id = (
                            jwt_data.get("email")
                            or jwt_data.get("preferred_username")
                            or jwt_data.get("sub")
                            or "unknown"
                    )
                    user_info = {
                        "sub": jwt_data.get("sub"),
                        "email": jwt_data.get("email"),
                        "preferred_username": jwt_data.get("preferred_username"),
                        "name": jwt_data.get("name"),
                        "given_name": jwt_data.get("given_name"),
                        "family_name": jwt_data.get("family_name"),
                        "exp": jwt_data.get("exp"),
                        "iat": jwt_data.get("iat"),
                        "scope": jwt_data.get("scope"),
                        "session_state": jwt_data.get("sid"),
                    }
                    user_info = {key: value for key, value in user_info.items() if value is not None}
                    logger.info(f"成功解析JWT，用户: {user_id}")
                    logger.debug(f"JWT用户信息: {user_info}")
                except (json.JSONDecodeError, UnicodeDecodeError) as decode_error:
                    logger.warning(f"JWT payload解码失败: {decode_error}")
                    user_id = fallback_user_id
            else:
                logger.warning("Bearer token为空或格式无效")
                user_id = fallback_user_id
        except Exception as e:
            logger.error(f"JWT解析过程发生异常: {e}")
            user_id = fallback_user_id

        return user_id, user_info


class CodeBuddyTokenSaver:
    """保存 CodeBuddy token 到当前系统用户的凭证目录。"""

    async def save(self, token_data: Dict[str, Any], owner_user: AuthenticatedUser) -> bool:
        try:
            from .codebuddy_token_manager import get_token_manager_for_user

            credential_data = TokenParser.build_credential_data(token_data)
            user_id = credential_data.get("user_id", "unknown")
            timestamp = int(time.time())
            safe_user_id = "".join(c for c in str(user_id) if c.isalnum() or c in "._-")[:20]
            filename = f"codebuddy_{safe_user_id}_{timestamp}.json"

            token_manager = get_token_manager_for_user(owner_user)
            success = token_manager.add_credential_with_data(
                credential_data=credential_data,
                filename=filename,
            )

            if success:
                logger.info(f"成功保存CodeBuddy token，用户: {user_id}，文件: {filename}")

            return success
        except Exception as e:
            logger.error(f"保存CodeBuddy token失败: {e}")
            return False


codebuddy_auth_client = CodeBuddyAuthClient()
codebuddy_token_saver = CodeBuddyTokenSaver()


def remember_auth_state(auth_state: str, user: AuthenticatedUser) -> bool:
    return auth_state_store.remember(auth_state, user)


def validate_auth_state_owner(auth_state: str, user: AuthenticatedUser) -> bool:
    return auth_state_store.validate_owner(auth_state, user)


def consume_auth_state(auth_state: str, user: AuthenticatedUser) -> bool:
    return auth_state_store.consume(auth_state, user)


async def start_codebuddy_auth() -> Dict[str, Any]:
    return await codebuddy_auth_client.start_auth()


async def poll_codebuddy_auth_status(auth_state: str) -> Dict[str, Any]:
    return await codebuddy_auth_client.poll_status(auth_state)


async def save_codebuddy_token(token_data: Dict[str, Any], owner_user: AuthenticatedUser) -> bool:
    return await codebuddy_token_saver.save(token_data, owner_user)
