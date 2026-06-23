"""CodeBuddy 模型列表查询与合并。"""
import logging
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

    def __init__(self, http_client_factory: Optional[Callable[..., Any]] = None):
        self._http_client_factory = http_client_factory or httpx.AsyncClient
        self._models_cache: Dict[str, List[str]] = {}

    async def get_available_models(self, user: AuthenticatedUser) -> List[str]:
        """返回配置模型与真实模型的并集，配置模型优先。"""
        configured_models = get_available_models()
        cache_key = user.username

        try:
            actual_models = await self._fetch_models_from_codebuddy(user)
            self._models_cache[cache_key] = actual_models
        except Exception as e:
            logger.warning("查询 CodeBuddy 真实模型列表失败，使用本地配置和缓存回退: %s", e)
            actual_models = self._models_cache.get(cache_key, [])

        return _ordered_union(configured_models, actual_models)

    async def _fetch_models_from_codebuddy(self, user: AuthenticatedUser) -> List[str]:
        token_manager = get_token_manager_for_user(user)
        credential = token_manager.get_next_credential()
        if not credential:
            raise RuntimeError("没有可用的 CodeBuddy 凭证")

        bearer_token = credential.get("bearer_token")
        if not bearer_token:
            raise RuntimeError("CodeBuddy 凭证缺少 bearer_token")

        headers = codebuddy_api_client.generate_codebuddy_headers(
            bearer_token=bearer_token,
            user_id=credential.get("user_id"),
            domain=credential.get("domain"),
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
