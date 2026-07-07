"""CodeBuddy 模型列表查询与合并。"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional

import httpx

from config import get_available_models, get_ssl_verify
from .auth_types import AuthenticatedUser
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import get_token_manager_for_user

logger = logging.getLogger(__name__)

_CONFIG_API_URL = "https://copilot.tencent.com/v3/config"
_CONFIG_API_HOST = "copilot.tencent.com"
_CODEBUDDY_IDE_VERSION = "4.9.13"
_MODEL_CACHE_TTL_SECONDS = 600.0


def _ordered_unique(models: List[str]) -> List[str]:
    result = []
    seen = set()
    for model in models:
        model_id = str(model or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        result.append(model_id)
    return result


def _ordered_union(configured_models: List[str], actual_models: List[str]) -> List[str]:
    return _ordered_unique([*configured_models, *actual_models])


class ModelsManager:
    """按用户查询真实模型列表，并与本地配置模型合并。"""

    def __init__(
            self,
            http_client_factory: Optional[Callable[..., Any]] = None,
            monotonic_factory: Callable[[], float] = time.monotonic,
            cache_ttl_seconds: float = _MODEL_CACHE_TTL_SECONDS,
    ):
        self._http_client_factory = http_client_factory or httpx.AsyncClient
        self._monotonic_factory = monotonic_factory
        self._cache_ttl_seconds = cache_ttl_seconds
        self._models_cache: Dict[str, List[str]] = {}
        self._models_cache_expires_at: Dict[str, float] = {}

    async def get_available_models(self, user: AuthenticatedUser) -> List[str]:
        """返回配置模型与真实模型的并集，配置模型优先。"""
        configured_models = get_available_models(user)

        try:
            actual_models = await self.get_actual_models(user)
        except Exception as e:
            logger.warning("查询 CodeBuddy 真实模型列表失败，使用本地配置回退: %s", e)
            actual_models = []

        return _ordered_union(configured_models, actual_models)

    async def get_actual_models(self, user: AuthenticatedUser) -> List[str]:
        """返回当前凭证的真实模型列表，10 分钟内复用同一凭证缓存。"""
        token_manager = get_token_manager_for_user(user)
        current_credential_id = self._current_credential_id(token_manager)
        if current_credential_id:
            cached_models = self._fresh_cached_models(self._credential_cache_key(user, current_credential_id))
            if cached_models is not None:
                current_credential = token_manager.get_credential_by_id(current_credential_id)
                if current_credential is not None and not token_manager.is_token_expired(current_credential):
                    return cached_models

        credential = token_manager.get_next_credential()
        if not credential:
            raise RuntimeError("没有可用的 CodeBuddy 凭证")

        credential_id = self._current_credential_id(token_manager)
        if not credential_id:
            raise RuntimeError("CodeBuddy 凭证缺少 credential_id")
        return await self.get_actual_models_for_credential(user, credential_id, credential)

    async def get_actual_models_for_credential(
            self,
            user: AuthenticatedUser,
            credential_id: str,
            credential: Dict[str, Any],
    ) -> List[str]:
        """返回指定凭证的真实模型列表，缓存和 TTL 均按 credential_id 隔离。"""
        cache_key = self._credential_cache_key(user, credential_id)
        cached_models = self._fresh_cached_models(cache_key)
        if cached_models is not None:
            return cached_models

        try:
            actual_models = await self._fetch_models_from_codebuddy_credential(credential)
        except Exception:
            stale_models = self._models_cache.get(cache_key)
            if stale_models is not None:
                return stale_models
            raise

        self._models_cache[cache_key] = actual_models
        self._models_cache_expires_at[cache_key] = self._monotonic_factory() + self._cache_ttl_seconds
        return actual_models

    async def get_first_actual_model(self, user: AuthenticatedUser) -> str:
        """返回 CodeBuddy 配置接口真实模型列表中的第一个模型。"""
        models = await self.get_actual_models(user)

        if not models:
            raise RuntimeError("CodeBuddy 配置接口没有可用模型")
        return models[0]

    async def get_first_actual_model_for_credential(
            self,
            user: AuthenticatedUser,
            credential_id: str,
            credential: Dict[str, Any],
    ) -> str:
        """返回指定凭证配置接口真实模型列表中的第一个模型。"""
        models = await self.get_actual_models_for_credential(user, credential_id, credential)

        if not models:
            raise RuntimeError("CodeBuddy 配置接口没有可用模型")
        return models[0]

    @staticmethod
    def _credential_cache_key(user: AuthenticatedUser, credential_id: str) -> str:
        normalized_id = str(credential_id or "").strip()
        if not normalized_id:
            raise RuntimeError("CodeBuddy 凭证缺少 credential_id")
        return f"{user.username}:{normalized_id}"

    @staticmethod
    def _current_credential_id(token_manager) -> Optional[str]:
        current_info = token_manager.get_current_credential_info()
        credential_id = current_info.get("credential_id") if isinstance(current_info, dict) else None
        if credential_id is None:
            return None
        return str(credential_id)

    def _fresh_cached_models(self, cache_key: str) -> Optional[List[str]]:
        cached_models = self._models_cache.get(cache_key)
        if cached_models is None:
            return None
        expires_at = self._models_cache_expires_at.get(cache_key, 0.0)
        if self._monotonic_factory() >= expires_at:
            return None
        return cached_models

    async def _fetch_models_from_codebuddy_credential(self, credential: Dict[str, Any]) -> List[str]:
        bearer_token = credential.get("bearer_token")
        if not bearer_token:
            raise RuntimeError("CodeBuddy 凭证缺少 bearer_token")

        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=bearer_token,
            user_id=credential.get("user_id"),
            domain=credential.get("domain"),
            enterprise_id=credential.get("enterprise_id"),
        )
        self._apply_config_api_headers(headers)

        timeout = httpx.Timeout(30.0, connect=10.0, read=30.0)
        async with self._http_client_factory(
                timeout=timeout,
                verify=get_ssl_verify(),
                trust_env=False,
        ) as client:
            response = await client.get(_CONFIG_API_URL, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(
                f"CodeBuddy 配置接口返回 HTTP {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(
                f"CodeBuddy 配置接口返回错误: code={body.get('code')}, msg={body.get('msg')}"
            )

        return self._extract_model_ids(body)

    @staticmethod
    def _apply_config_api_headers(headers: Dict[str, str]) -> None:
        headers.update({
            "Host": _CONFIG_API_HOST,
            "X-Domain": _CONFIG_API_HOST,
            "Accept": "application/json",
            "X-IDE-Type": "CodeBuddyIDE",
            "X-IDE-Name": "CodeBuddyIDE",
            "X-IDE-Version": _CODEBUDDY_IDE_VERSION,
            "X-Product-Version": _CODEBUDDY_IDE_VERSION,
        })

    @staticmethod
    def _extract_model_ids(body: Dict[str, Any]) -> List[str]:
        data = body.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("CodeBuddy 配置接口返回 data 不是对象")

        raw_models = data.get("models")
        if not isinstance(raw_models, list):
            raise RuntimeError("CodeBuddy 配置接口未返回有效 models 列表")

        model_ids = []
        for item in raw_models:
            if not isinstance(item, dict):
                raise RuntimeError("CodeBuddy 配置接口 models 包含非对象项")
            model_id = str(item.get("id", "")).strip()
            if model_id:
                model_ids.append(model_id)

        models = _ordered_unique(model_ids)
        if not models:
            raise RuntimeError("CodeBuddy 配置接口返回的 models 没有有效 id")
        return models


models_manager = ModelsManager()
