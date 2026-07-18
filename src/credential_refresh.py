"""CodeBuddy OAuth 凭证后台刷新。"""
import asyncio
import inspect
import logging
import time
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

import httpx

from config import get_codebuddy_api_endpoint
from .auth_types import AuthenticatedUser
from .codebuddy_oauth import CodeBuddyAuthClient, TokenParser, get_codebuddy_accounts_endpoint
from .codebuddy_token_manager import CodeBuddyTokenManagerRegistry, codebuddy_token_managers
from .stream_service import get_http_client
from .users_store import users_store

logger = logging.getLogger(__name__)
REFRESH_WINDOW_SECONDS = 86400
REFRESH_INTERVAL_SECONDS = 3600


class CredentialRefreshError(RuntimeError):
    """后台刷新失败，不携带上游响应体。"""


class CredentialRefreshManager:
    """启动后及每小时刷新进入 24 小时窗口的 OAuth 凭证。"""

    def __init__(
            self,
            *,
            registry: CodeBuddyTokenManagerRegistry = codebuddy_token_managers,
            usernames_provider: Callable[[], Iterable[str]] = users_store.list_usernames,
            http_client_factory: Callable[[], Any] = get_http_client,
            now_factory: Callable[[], float] = time.time,
            interval_seconds: float = REFRESH_INTERVAL_SECONDS,
            retry_delays: Sequence[float] = (5, 10, 20, 40, 60),
            max_concurrency: int = 4,
    ) -> None:
        self._registry = registry
        self._usernames_provider = usernames_provider
        self._http_client_factory = http_client_factory
        self._now_factory = now_factory
        self._interval_seconds = interval_seconds
        self._retry_delays = tuple(retry_delays)
        self._max_concurrency = max_concurrency
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._inflight: Dict[str, asyncio.Task] = {}

    async def _get_http_client(self):
        client = self._http_client_factory()
        return await client if inspect.isawaitable(client) else client

    async def startup(self) -> None:
        if self._task is not None:
            raise RuntimeError("credential refresh manager is already running")
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        task = self._task
        stop_event = self._stop_event
        if task is None or stop_event is None:
            return
        stop_event.set()
        try:
            await task
        finally:
            self._task = None
            self._stop_event = None

    async def _run(self) -> None:
        while self._stop_event is not None:
            try:
                await self.scan_once()
            except Exception:
                logger.exception("CodeBuddy 凭证后台刷新扫描失败")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
                return
            except TimeoutError:
                pass

    @staticmethod
    def _expires_at(credential: Dict[str, Any]) -> Optional[int]:
        expires_at = credential.get("expires_at")
        if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
            return int(expires_at)
        created_at = credential.get("created_at")
        expires_in = credential.get("expires_in")
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (created_at, expires_in)):
            return int(created_at + expires_in)
        return None

    def _should_refresh(self, credential: Dict[str, Any]) -> bool:
        refresh_token = credential.get("refresh_token")
        expires_at = self._expires_at(credential)
        if not isinstance(refresh_token, str) or not refresh_token or expires_at is None:
            return False
        now = int(self._now_factory())
        refresh_expires_at = credential.get("refresh_expires_at")
        if isinstance(refresh_expires_at, (int, float)) and now >= int(refresh_expires_at):
            return False
        return now >= expires_at - REFRESH_WINDOW_SECONDS

    async def scan_once(self) -> None:
        semaphore = asyncio.Semaphore(self._max_concurrency)
        jobs = []
        for username in self._usernames_provider():
            manager = self._registry.for_username(username)
            for info in manager.get_credentials_info():
                credential_id = info["credential_id"]
                snapshot = manager.snapshot_credential_by_id(credential_id)
                if snapshot is None or not self._should_refresh(snapshot[0]):
                    continue
                jobs.append(self._refresh_with_semaphore(
                    semaphore, username, manager, credential_id, snapshot,
                ))
        if jobs:
            await asyncio.gather(*jobs)

    async def _refresh_with_semaphore(self, semaphore, username, manager, credential_id, snapshot):
        async with semaphore:
            try:
                await self.refresh_credential(username, manager, credential_id, snapshot)
            except CredentialRefreshError:
                logger.warning("CodeBuddy 凭证刷新失败：用户=%s，凭证=%s", username, credential_id)

    async def refresh_credential(self, username, manager, credential_id, snapshot=None) -> bool:
        key = f"{username}:{credential_id}"
        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)
        if snapshot is None:
            snapshot = manager.snapshot_credential_by_id(credential_id)
        if snapshot is None:
            return False
        task = asyncio.create_task(self._perform_refresh(username, manager, credential_id, snapshot))
        self._inflight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def _perform_refresh(self, username, manager, credential_id, snapshot) -> bool:
        credential, generation = snapshot
        client = await self._get_http_client()
        delays = (0, *self._retry_delays)
        last_error = None
        for attempt, delay in enumerate(delays):  # pragma: no branch - delays 始终包含首次尝试
            if delay:
                await asyncio.sleep(delay)
            try:
                return await self._refresh_once(
                    client, username, manager, credential_id, credential, generation,
                )
            except CredentialRefreshError as error:
                last_error = error
                if str(error) == "unauthorized" or attempt == len(delays) - 1:
                    break
            except (httpx.HTTPError, TimeoutError) as error:
                last_error = error
                if attempt == len(delays) - 1:
                    break
        raise CredentialRefreshError("refresh_failed") from last_error

    async def _refresh_once(self, client, username, manager, credential_id, credential, generation) -> bool:
        token_data = {
            "access_token": credential["bearer_token"],
            "domain": credential.get("domain"),
        }
        headers = CodeBuddyAuthClient._auth_headers(token_data)
        headers["X-Refresh-Token"] = credential["refresh_token"]
        headers["X-Auth-Refresh-Source"] = "plugin"
        response = await client.post(
            f"{get_codebuddy_api_endpoint()}/v2/plugin/auth/token/refresh",
            json={},
            headers=headers,
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise CredentialRefreshError("unauthorized")
        if response.status_code >= 500:
            raise CredentialRefreshError("temporary")
        try:
            body = response.json()
        except ValueError as error:
            raise CredentialRefreshError("invalid_response") from error
        if response.status_code != 200 or not isinstance(body, dict) or body.get("code") != 0:
            raise CredentialRefreshError("invalid_response")
        refreshed = CodeBuddyAuthClient._token_data(body)
        if refreshed is None:
            raise CredentialRefreshError("invalid_response")

        accounts_response = await client.get(
            get_codebuddy_accounts_endpoint(),
            headers=CodeBuddyAuthClient._auth_headers(refreshed),
            timeout=30,
        )
        try:
            accounts_body = accounts_response.json()
        except ValueError as error:
            raise CredentialRefreshError("invalid_accounts") from error
        accounts_data = accounts_body.get("data") if isinstance(accounts_body, dict) else None
        accounts = accounts_data.get("accounts") if isinstance(accounts_data, dict) else None
        if (
                accounts_response.status_code != 200
                or not isinstance(accounts_body, dict)
                or accounts_body.get("code") != 0
                or not isinstance(accounts, list)
        ):
            raise CredentialRefreshError("invalid_accounts")
        enabled = [item for item in accounts if isinstance(item, dict) and item.get("pluginEnabled") is True]
        current_account_id = credential.get("account_id")
        selected = next(
            (item for item in enabled if TokenParser._account_id(item) == current_account_id),
            None,
        )
        if selected is None and current_account_id is None:
            selected = next((item for item in enabled if item.get("lastLogin") is True), None)
            selected = selected or (enabled[0] if enabled else None)
        if selected is None:
            raise CredentialRefreshError("account_missing")

        upstream = dict(credential.get("upstream_responses") or {})
        upstream["refresh"] = body
        upstream["accounts"] = accounts_body
        refreshed.update({
            "domain": refreshed.get("domain") or credential.get("domain"),
            "enterprise_id": refreshed.get("enterprise_id") or credential.get("enterprise_id"),
            "account": selected,
            "accounts": enabled,
            "upstream_responses": upstream,
            "last_refresh_at": int(self._now_factory()),
        })
        updated = TokenParser.build_credential_data(refreshed)
        if credential.get("compatibility_data") is not None:
            updated["compatibility_data"] = credential["compatibility_data"]
        from .models_manager import models_manager

        models_manager.invalidate_credential(
            AuthenticatedUser(username=username, source="background_refresh"),
            credential_id,
        )
        if not manager.replace_credential_by_id(
            credential_id,
            updated,
            expected_generation=generation,
        ):
            return False
        return True

    async def switch_account(self, username, manager, credential_id, account_id) -> bool:
        """使用服务端保存的账号上下文切换个人或企业账号。"""
        key = f"{username}:{credential_id}"
        existing = self._inflight.get(key)
        if existing is not None:
            await asyncio.shield(existing)
        snapshot = manager.snapshot_credential_by_id(credential_id)
        if snapshot is None:
            raise CredentialRefreshError("credential_not_found")
        task = asyncio.create_task(
            self._perform_switch(username, manager, credential_id, account_id, snapshot)
        )
        self._inflight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def _perform_switch(self, username, manager, credential_id, account_id, snapshot) -> bool:
        credential, generation = snapshot
        refresh_token = credential.get("refresh_token")
        accounts = credential.get("accounts") or []
        target = next(
            (item for item in accounts if item.get("account_id") == account_id),
            None,
        )
        if not isinstance(refresh_token, str) or not refresh_token:
            raise CredentialRefreshError("switch_unavailable")
        if not isinstance(target, dict):
            raise CredentialRefreshError("account_not_found")

        account_type = target.get("type")
        enterprise_id = target.get("enterprise_id")
        path = "/v2/plugin/login/enterprise"
        if account_type != "personal":
            if not isinstance(enterprise_id, str) or not enterprise_id:
                raise CredentialRefreshError("account_invalid")
            path = f"{path}/{enterprise_id}"
        headers = CodeBuddyAuthClient._auth_headers({
            "access_token": credential["bearer_token"],
            "domain": credential.get("domain"),
        })
        headers["X-Refresh-Token"] = refresh_token
        if enterprise_id:
            headers["X-Enterprise-Id"] = enterprise_id
            headers["X-Tenant-Id"] = enterprise_id
        client = await self._get_http_client()
        response = await client.post(
            f"{get_codebuddy_api_endpoint()}{path}",
            json={},
            headers=headers,
            timeout=30,
        )
        try:
            body = response.json()
        except ValueError as error:
            raise CredentialRefreshError("invalid_response") from error
        if isinstance(body, dict) and body.get("code") == 10081:
            raise CredentialRefreshError("ip_restricted")
        if response.status_code >= 500:
            raise CredentialRefreshError("temporary")
        if response.status_code != 200 or not isinstance(body, dict) or body.get("code") != 0:
            raise CredentialRefreshError("invalid_response")
        switched = CodeBuddyAuthClient._token_data(body)
        if switched is None:
            raise CredentialRefreshError("invalid_response")

        accounts_response = await client.get(
            get_codebuddy_accounts_endpoint(),
            headers=CodeBuddyAuthClient._auth_headers(switched),
            timeout=30,
        )
        try:
            accounts_body = accounts_response.json()
        except ValueError as error:
            raise CredentialRefreshError("invalid_accounts") from error
        accounts_data = accounts_body.get("data") if isinstance(accounts_body, dict) else None
        raw_accounts = accounts_data.get("accounts") if isinstance(accounts_data, dict) else None
        if (
                accounts_response.status_code != 200
                or not isinstance(accounts_body, dict)
                or accounts_body.get("code") != 0
                or not isinstance(raw_accounts, list)
        ):
            raise CredentialRefreshError("invalid_accounts")
        enabled = [item for item in raw_accounts if isinstance(item, dict) and item.get("pluginEnabled") is True]
        selected = next((item for item in enabled if TokenParser._account_id(item) == account_id), None)
        if selected is None:
            raise CredentialRefreshError("account_missing")

        upstream = dict(credential.get("upstream_responses") or {})
        upstream["account_switch"] = body
        upstream["accounts"] = accounts_body
        switched.update({
            "domain": switched.get("domain") or credential.get("domain"),
            "enterprise_id": switched.get("enterprise_id") or credential.get("enterprise_id"),
            "account": selected,
            "accounts": enabled,
            "upstream_responses": upstream,
            "last_refresh_at": int(self._now_factory()),
        })
        updated = TokenParser.build_credential_data(switched)
        if credential.get("compatibility_data") is not None:
            updated["compatibility_data"] = credential["compatibility_data"]
        from .models_manager import models_manager

        models_manager.invalidate_credential(
            AuthenticatedUser(username=username, source="account_switch"),
            credential_id,
        )
        if not manager.replace_credential_by_id(
            credential_id,
            updated,
            expected_generation=generation,
        ):
            raise CredentialRefreshError("generation_conflict")
        return True


credential_refresh_manager = CredentialRefreshManager()
