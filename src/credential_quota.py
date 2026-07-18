"""CodeBuddy 凭证额度探测、缓存与请求后估算。"""

import asyncio
import inspect
import logging
import math
import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional

import httpx

from config import get_codebuddy_api_endpoint
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import CodeBuddyTokenManagerRegistry, codebuddy_token_managers
from .stream_service import get_http_client
from .users_store import users_store

logger = logging.getLogger(__name__)

QUOTA_REFRESH_INTERVAL_SECONDS = 3600.0
QUOTA_MAX_CONCURRENCY = 4
QUOTA_PRODUCT_CODE = "p_tcaca"
QUOTA_RANGE_END = "2127-01-01 00:00:00"


class CredentialQuotaProbeError(RuntimeError):
    """额度探测失败，仅携带受控错误类别。"""


def _unknown_quota() -> Dict[str, Any]:
    return {
        "status": "unknown",
        "total": None,
        "remaining": None,
        "remaining_percent": None,
        "estimated": False,
        "estimated_credit_since_sync": 0,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_estimated_at": None,
        "error_type": None,
        "packages": [],
    }


def _nonnegative_number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CredentialQuotaProbeError("invalid_response")
    if not math.isfinite(value) or value < 0:
        raise CredentialQuotaProbeError("invalid_response")
    return value


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CredentialQuotaProbeError("invalid_response")
    return value


def _remaining_percent(remaining: float | int, total: float | int) -> int:
    if total <= 0:
        return 0
    return int(round(max(0.0, min(100.0, float(remaining) / float(total) * 100))))


class CredentialQuotaManager:
    """按系统用户与稳定凭证 ID 隔离额度状态。"""

    def __init__(
            self,
            *,
            registry: CodeBuddyTokenManagerRegistry = codebuddy_token_managers,
            usernames_provider: Callable[[], Iterable[str]] = users_store.list_usernames,
            http_client_factory: Callable[[], Any] = get_http_client,
            now_factory: Callable[[], float] = time.time,
            interval_seconds: float = QUOTA_REFRESH_INTERVAL_SECONDS,
            max_concurrency: int = QUOTA_MAX_CONCURRENCY,
    ) -> None:
        self._registry = registry
        self._usernames_provider = usernames_provider
        self._http_client_factory = http_client_factory
        self._now_factory = now_factory
        self._interval_seconds = interval_seconds
        self._max_concurrency = max_concurrency
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._usage_totals: Dict[str, float] = {}
        self._invalidations: Dict[str, int] = {}
        self._inflight: Dict[str, asyncio.Task] = {}
        self._scheduled: set[asyncio.Task] = set()
        self._lock = threading.RLock()
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    @staticmethod
    def _cache_key(username: str, credential_id: str) -> str:
        normalized_username = str(username or "").strip()
        normalized_id = str(credential_id or "").strip()
        if not normalized_username or not normalized_id:
            raise ValueError("username and credential_id are required")
        return f"{normalized_username}:{normalized_id}"

    async def _get_http_client(self):
        client = self._http_client_factory()
        return await client if inspect.isawaitable(client) else client

    async def startup(self) -> None:
        if self._task is not None:
            raise RuntimeError("credential quota manager is already running")
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
            scheduled = tuple(self._scheduled)
            for item in scheduled:
                item.cancel()
            if scheduled:
                await asyncio.gather(*scheduled, return_exceptions=True)
            inflight = tuple(set(self._inflight.values()))
            for item in inflight:
                item.cancel()
            if inflight:
                await asyncio.gather(*inflight, return_exceptions=True)
            self._inflight.clear()
            self._task = None
            self._stop_event = None

    async def _run(self) -> None:
        while True:
            try:
                await self.scan_once()
            except Exception:
                logger.exception("CodeBuddy 凭证额度后台扫描失败")
            if self._stop_event.is_set():
                return
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval_seconds,
                )
                return
            except TimeoutError:
                pass

    async def scan_once(self) -> None:
        semaphore = asyncio.Semaphore(self._max_concurrency)
        jobs = []
        for username in self._usernames_provider():
            manager = self._registry.for_username(username)
            for info in manager.get_credentials_info():
                credential_id = info.get("credential_id")
                if not credential_id or info.get("is_expired"):
                    continue
                jobs.append(self._probe_with_semaphore(
                    semaphore, username, manager, credential_id,
                ))
        if jobs:
            await asyncio.gather(*jobs)

    async def _probe_with_semaphore(self, semaphore, username, manager, credential_id):
        async with semaphore:
            await self.probe_credential(username, manager, credential_id)

    async def probe_credential(self, username, manager, credential_id):
        key = self._cache_key(username, credential_id)
        existing = self._inflight.get(key)
        if existing is None:
            existing = asyncio.create_task(
                self._perform_probe(username, manager, credential_id, key),
            )
            self._inflight[key] = existing
            existing.add_done_callback(
                lambda completed: self._remove_completed_inflight(key, completed)
            )
        return await asyncio.shield(existing)

    def _remove_completed_inflight(self, key: str, task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)

    async def _perform_probe(self, username, manager, credential_id, key):
        credential = manager.get_credential_by_id(credential_id)
        if credential is None or manager.is_token_expired(credential):
            return None
        with self._lock:
            usage_at_start = self._usage_totals.get(key, 0.0)
            invalidation_at_start = self._invalidations.get(key, 0)
        attempted_at = int(self._now_factory())
        try:
            snapshot = await self._fetch_quota(credential)
        except CredentialQuotaProbeError as error:
            return self._record_failure(key, attempted_at, str(error))
        except (httpx.HTTPError, TimeoutError):
            return self._record_failure(key, attempted_at, "transport_error")
        except Exception:
            logger.error("CodeBuddy 凭证额度探测发生内部错误：用户=%s，凭证=%s", username, credential_id)
            return self._record_failure(key, attempted_at, "invalid_response")

        with self._lock:
            if self._invalidations.get(key, 0) != invalidation_at_start:
                return deepcopy(self._cache.get(key, _unknown_quota()))
            concurrent_credit = max(0.0, self._usage_totals.get(key, 0.0) - usage_at_start)
            remaining = max(0.0, float(snapshot["remaining"]) - concurrent_credit)
            if isinstance(snapshot["remaining"], int) and concurrent_credit == 0:
                remaining = snapshot["remaining"]
            entry = {
                **snapshot,
                "status": "fresh",
                "remaining": remaining,
                "remaining_percent": _remaining_percent(remaining, snapshot["total"]),
                "estimated": concurrent_credit > 0,
                "estimated_credit_since_sync": concurrent_credit,
                "last_attempt_at": attempted_at,
                "last_success_at": attempted_at,
                "last_estimated_at": attempted_at if concurrent_credit > 0 else None,
                "error_type": None,
            }
            self._cache[key] = entry
            return deepcopy(entry)

    async def _fetch_quota(self, credential: Dict[str, Any]) -> Dict[str, Any]:
        bearer_token = credential.get("bearer_token")
        if not bearer_token:
            raise CredentialQuotaProbeError("authentication_error")
        try:
            headers = codebuddy_api_client.generate_codebuddy_headers(
                bearer_token=bearer_token,
                user_id=credential.get("user_id"),
                account_uid=credential.get("account_uid"),
                domain=credential.get("domain"),
                enterprise_id=credential.get("enterprise_id"),
                department_full_name=credential.get("department_full_name"),
            )
        except (TypeError, ValueError) as error:
            raise CredentialQuotaProbeError("authentication_error") from error
        headers["Accept"] = "application/json, text/plain, */*"
        is_enterprise = bool(credential.get("enterprise_id"))
        if is_enterprise:
            path = "/v2/billing/meter/get-enterprise-user-usage"
            payload = {}
        else:
            path = "/v2/billing/meter/get-user-resource"
            current_time = datetime.fromtimestamp(self._now_factory()).astimezone()
            payload = {
                "PageNumber": 1,
                "PageSize": 200,
                "ProductCode": QUOTA_PRODUCT_CODE,
                "Status": [0, 3],
                "PackageEndTimeRangeBegin": current_time.strftime("%Y-%m-%d %H:%M:%S"),
                "PackageEndTimeRangeEnd": QUOTA_RANGE_END,
            }
        client = await self._get_http_client()
        response = await client.post(
            f"{get_codebuddy_api_endpoint()}{path}",
            json=payload,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=10.0, read=30.0),
        )
        if response.status_code in (401, 403):
            raise CredentialQuotaProbeError("authentication_error")
        if response.status_code != 200:
            raise CredentialQuotaProbeError("upstream_unavailable")
        try:
            body = response.json()
        except Exception as error:
            raise CredentialQuotaProbeError("invalid_response") from error
        if is_enterprise:
            return self._parse_enterprise_response(body)
        return self._parse_response(body)

    @staticmethod
    def _parse_enterprise_response(body: Any) -> Dict[str, Any]:
        if not isinstance(body, dict) or body.get("code") != 0:
            raise CredentialQuotaProbeError("invalid_response")
        data = body.get("data")
        if not isinstance(data, dict):
            raise CredentialQuotaProbeError("invalid_response")
        used = _nonnegative_number(data.get("credit"))
        total = _nonnegative_number(data.get("limitNum"))
        remaining = max(0.0, float(total) - float(used))
        if isinstance(total, int) and isinstance(used, int) and used <= total:
            remaining = total - used
        package = {
            "name": "企业额度",
            "total": total,
            "remaining": remaining,
            "used": used,
            "cycle_start": _optional_text(data.get("cycleStartTime")),
            "cycle_end": _optional_text(data.get("cycleEndTime")),
        }
        return {
            "total": total,
            "remaining": remaining,
            "remaining_percent": _remaining_percent(remaining, total),
            "packages": [package],
        }

    @staticmethod
    def _parse_response(body: Any) -> Dict[str, Any]:
        if not isinstance(body, dict) or body.get("code") != 0:
            raise CredentialQuotaProbeError("invalid_response")
        data = body.get("data")
        response_data = data.get("Response") if isinstance(data, dict) else None
        account_data = response_data.get("Data") if isinstance(response_data, dict) else None
        accounts = account_data.get("Accounts") if isinstance(account_data, dict) else None
        if (
                accounts is None
                and isinstance(account_data, dict)
                and type(account_data.get("TotalCount")) is int
                and account_data["TotalCount"] == 0
        ):
            accounts = []
        if not isinstance(accounts, list):
            raise CredentialQuotaProbeError("invalid_response")

        total: float | int = 0
        remaining: float | int = 0
        packages = []
        for account in accounts:
            if not isinstance(account, dict):
                raise CredentialQuotaProbeError("invalid_response")
            if account.get("Status") != 0:
                continue
            package_total = _nonnegative_number(account.get("CapacitySize"))
            package_remaining = _nonnegative_number(account.get("CapacityRemain"))
            package_used = _nonnegative_number(account.get("CapacityUsed"))
            name = account.get("PackageName", "未命名套餐")
            if not isinstance(name, str):
                raise CredentialQuotaProbeError("invalid_response")
            total += package_total
            remaining += package_remaining
            packages.append({
                "name": name,
                "total": package_total,
                "remaining": package_remaining,
                "used": package_used,
                "cycle_start": _optional_text(account.get("CycleStartTime")),
                "cycle_end": _optional_text(account.get("CycleEndTime")),
            })
        return {
            "total": total,
            "remaining": remaining,
            "remaining_percent": _remaining_percent(remaining, total),
            "packages": packages,
        }

    def _record_failure(self, key: str, attempted_at: int, error_type: str) -> Dict[str, Any]:
        logger.warning("CodeBuddy 凭证额度探测失败：凭证=%s，类型=%s", key, error_type)
        with self._lock:
            previous = self._cache.get(key)
            if previous is None or previous.get("last_success_at") is None:
                entry = {
                    **_unknown_quota(),
                    "status": "error",
                    "last_attempt_at": attempted_at,
                    "error_type": error_type,
                }
            else:
                entry = {
                    **previous,
                    "status": "stale",
                    "last_attempt_at": attempted_at,
                    "error_type": error_type,
                }
            self._cache[key] = entry
            return deepcopy(entry)

    def get_quota(self, username: str, credential_id: str) -> Dict[str, Any]:
        key = self._cache_key(username, credential_id)
        with self._lock:
            return deepcopy(self._cache.get(key, _unknown_quota()))

    def apply_usage(
            self,
            username: str,
            credential_id: str,
            credit: Any,
            *,
            occurred_at: Optional[int] = None,
    ) -> None:
        if (
                isinstance(credit, bool)
                or not isinstance(credit, (int, float))
                or not math.isfinite(credit)
                or credit < 0
        ):
            return
        key = self._cache_key(username, credential_id)
        numeric_credit = float(credit)
        with self._lock:
            self._usage_totals[key] = self._usage_totals.get(key, 0.0) + numeric_credit
            entry = self._cache.get(key)
            if entry is None or entry.get("remaining") is None:
                return
            remaining = max(0.0, float(entry["remaining"]) - numeric_credit)
            entry["remaining"] = remaining
            entry["remaining_percent"] = _remaining_percent(remaining, entry["total"])
            entry["estimated_credit_since_sync"] = (
                float(entry.get("estimated_credit_since_sync", 0)) + numeric_credit
            )
            entry["estimated"] = entry["estimated_credit_since_sync"] > 0
            entry["last_estimated_at"] = int(
                occurred_at if occurred_at is not None else self._now_factory()
            )

    def invalidate_credential(self, username: str, credential_id: str) -> None:
        key = self._cache_key(username, credential_id)
        with self._lock:
            self._invalidations[key] = self._invalidations.get(key, 0) + 1
            self._cache.pop(key, None)
            self._usage_totals.pop(key, None)
        self._inflight.pop(key, None)

    def schedule_probe(self, username: str, manager, credential_id: str) -> asyncio.Task:
        async def run_safely():
            try:
                return await self.probe_credential(username, manager, credential_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "CodeBuddy 凭证额度事件探测失败：用户=%s，凭证=%s",
                    username,
                    credential_id,
                )
                return None

        task = asyncio.create_task(run_safely())
        self._scheduled.add(task)
        task.add_done_callback(self._scheduled.discard)
        return task

    def schedule_probe_if_running(self, username: str, manager, credential_id: str) -> Optional[asyncio.Task]:
        """仅在后台管理器已启动时安排事件触发的探测。"""
        if self._task is None:
            return None
        return self.schedule_probe(username, manager, credential_id)

    def seed_quota_for_tests(
            self,
            username: str,
            credential_id: str,
            *,
            total: float,
            remaining: float,
    ) -> None:
        key = self._cache_key(username, credential_id)
        with self._lock:
            self._cache[key] = {
                **_unknown_quota(),
                "status": "fresh",
                "total": total,
                "remaining": remaining,
                "remaining_percent": _remaining_percent(remaining, total),
                "last_attempt_at": int(self._now_factory()),
                "last_success_at": int(self._now_factory()),
            }


credential_quota_manager = CredentialQuotaManager()
