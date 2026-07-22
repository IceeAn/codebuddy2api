"""CodeBuddy 个人账号每日签到、持久化与调度。"""

import asyncio
import hashlib
import inspect
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timedelta, tzinfo
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Union

import httpx

from config import (
    get_auto_checkin_enabled,
    get_codebuddy_api_endpoint,
    get_database_path,
)
from .codebuddy_api_client import codebuddy_api_client
from .codebuddy_token_manager import CodeBuddyTokenManagerRegistry, codebuddy_token_managers
from .credential_quota import CredentialQuotaManager, credential_quota_manager
from .sqlite_database import SQLiteDatabase
from .stream_service import get_http_client
from .users_store import users_store

logger = logging.getLogger(__name__)

CHECKIN_HOUR = 9
CHECKIN_MINUTE = 30
CHECKIN_MAX_CONCURRENCY = 4


class CredentialCheckinConflict(RuntimeError):
    """签到请求因凭证资格、今日状态或并发状态被拒绝。"""


def _local_datetime(timestamp: int, zone: Optional[tzinfo]) -> datetime:
    if zone is not None:
        return datetime.fromtimestamp(timestamp, zone)
    return datetime.fromtimestamp(timestamp)


def _local_timestamp(local_date: date, local_time: datetime_time, zone: Optional[tzinfo]) -> int:
    value = datetime.combine(local_date, local_time, tzinfo=zone)
    if zone is not None:
        return int(value.timestamp())
    return int(time.mktime(value.timetuple()))


def local_day_bounds(timestamp: int, zone: Optional[tzinfo] = None) -> tuple[int, int]:
    """返回时间戳所在服务器自然日的 UTC Unix 秒半开区间。"""
    local_date = _local_datetime(timestamp, zone).date()
    start = _local_timestamp(local_date, datetime_time.min, zone)
    end = _local_timestamp(local_date + timedelta(days=1), datetime_time.min, zone)
    return start, end


def _scheduled_at(local_date: date, zone: Optional[tzinfo]) -> int:
    return _local_timestamp(
        local_date,
        datetime_time(hour=CHECKIN_HOUR, minute=CHECKIN_MINUTE),
        zone,
    )


def account_key_for_credential(credential: Dict[str, Any], endpoint: str) -> str:
    """按真实 X-User-Id 与上游端点生成不可逆账号键。"""
    effective_user_id = credential.get("account_uid") or credential.get("user_id")
    if not isinstance(effective_user_id, str) or not effective_user_id:
        raise ValueError("CodeBuddy 凭证缺少有效的上游账号标识")
    canonical = json.dumps(
        [str(endpoint).rstrip("/"), effective_user_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CredentialCheckinStore:
    """在统一 SQLite 中保存每个上游账号最近一次签到。"""

    def __init__(self, database_path: Optional[Union[str, Path]] = None) -> None:
        self._database_path = Path(database_path) if database_path is not None else None

    def _database(self) -> SQLiteDatabase:
        return SQLiteDatabase(self._database_path or get_database_path())

    def save(self, record: Dict[str, Any]) -> None:
        values = dict(record)
        values["success"] = int(bool(values["success"]))
        with self._database().connect() as connection:
            connection.execute(
                """
                INSERT INTO credential_daily_checkins(
                    username, account_key, code, message, credit,
                    attempted_at, checked_in_at, success
                ) VALUES (
                    :username, :account_key, :code, :message, :credit,
                    :attempted_at, :checked_in_at, :success
                )
                ON CONFLICT(username, account_key) DO UPDATE SET
                    code = excluded.code,
                    message = excluded.message,
                    credit = excluded.credit,
                    attempted_at = excluded.attempted_at,
                    checked_in_at = excluded.checked_in_at,
                    success = excluded.success
                """,
                values,
            )

    def get(self, username: str, account_key: str) -> Optional[Dict[str, Any]]:
        database = self._database()
        if not database.path.exists():
            return None
        with database.connect() as connection:
            row = connection.execute(
                """
                SELECT code, message, credit, attempted_at, checked_in_at, success
                FROM credential_daily_checkins
                WHERE username = ? AND account_key = ?
                """,
                (username, account_key),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["success"] = bool(result["success"])
        return result

    def delete_outside(self, start_at: int, end_at: int) -> int:
        with self._database().connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM credential_daily_checkins
                WHERE attempted_at < ? OR attempted_at >= ?
                """,
                (start_at, end_at),
            )
            return cursor.rowcount


@dataclass
class _AccountGate:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    active: Optional[str] = None
    manual_waiter: bool = False
    auto_waiter: bool = False


class CredentialCheckinManager:
    """按上游账号串行化手动/自动签到，并负责每日调度。"""

    def __init__(
            self,
            *,
            registry: CodeBuddyTokenManagerRegistry = codebuddy_token_managers,
            usernames_provider: Callable[[], Iterable[str]] = users_store.list_usernames,
            http_client_factory: Callable[[], Any] = get_http_client,
            store: Optional[CredentialCheckinStore] = None,
            now_factory: Callable[[], float] = time.time,
            timezone: Optional[tzinfo] = None,
            max_concurrency: int = CHECKIN_MAX_CONCURRENCY,
            auto_enabled_provider: Callable[[str], bool] = get_auto_checkin_enabled,
            quota_manager: CredentialQuotaManager = credential_quota_manager,
    ) -> None:
        self._registry = registry
        self._usernames_provider = usernames_provider
        self._http_client_factory = http_client_factory
        self._store = store or CredentialCheckinStore()
        self._now_factory = now_factory
        self._timezone = timezone
        self._max_concurrency = max_concurrency
        self._auto_enabled_provider = auto_enabled_provider
        self._quota_manager = quota_manager
        self._gates: Dict[str, _AccountGate] = {}
        self._inflight_checkins: set[asyncio.Task] = set()
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._stopping = False

    async def _get_http_client(self):
        client = self._http_client_factory()
        return await client if inspect.isawaitable(client) else client

    @staticmethod
    def _gate_key(username: str, account_key: str) -> str:
        return f"{username}:{account_key}"

    def _today_record(self, username: str, account_key: str) -> Optional[Dict[str, Any]]:
        record = self._store.get(username, account_key)
        if record is None:
            return None
        start, end = local_day_bounds(int(self._now_factory()), self._timezone)
        if not start <= record["attempted_at"] < end:
            return None
        return record

    def _public_detail(self, record: Dict[str, Any]) -> Dict[str, Any]:
        result = {
            "code": record["code"],
            "message": record["message"],
            "success": bool(record["success"]),
        }
        if record["code"] == 0:
            result["credit"] = record["credit"]
            result["checked_in_at"] = record["checked_in_at"]
        if record["success"]:
            _start, next_midnight = local_day_bounds(
                record["attempted_at"], self._timezone,
            )
            result["next_checkin_at"] = next_midnight
        return result

    def today_detail_for_credential(
            self, username: str, credential: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not credential:
            return None
        try:
            account_key = account_key_for_credential(
                credential, get_codebuddy_api_endpoint(),
            )
        except ValueError:
            return None
        record = self._today_record(username, account_key)
        return self._public_detail(record) if record is not None else None

    def _eligible_snapshot(self, manager, credential_id: str) -> tuple[Dict[str, Any], str]:
        snapshot = manager.snapshot_credential_by_id(credential_id)
        if snapshot is None:
            raise CredentialCheckinConflict("credential_not_found")
        credential = snapshot[0]
        if manager.is_token_expired(credential) or credential.get("enterprise_id"):
            raise CredentialCheckinConflict("credential_ineligible")
        try:
            account_key = account_key_for_credential(
                credential, get_codebuddy_api_endpoint(),
            )
        except ValueError as error:
            raise CredentialCheckinConflict("credential_ineligible") from error
        return credential, account_key

    async def _claim(self, gate: _AccountGate, source: str) -> bool:
        async with gate.condition:
            if source == "manual":
                if gate.active == "manual" or gate.manual_waiter:
                    raise CredentialCheckinConflict("manual_in_progress")
                if gate.active == "auto" or gate.auto_waiter:
                    gate.manual_waiter = True
                    try:
                        while gate.active == "auto" or gate.auto_waiter:
                            await gate.condition.wait()
                    finally:
                        gate.manual_waiter = False
                gate.active = "manual"
                return True

            if gate.active == "auto" or gate.auto_waiter:
                return False
            if gate.active == "manual":
                gate.auto_waiter = True
                try:
                    while gate.active == "manual":
                        await gate.condition.wait()
                finally:
                    gate.auto_waiter = False
            gate.active = "auto"
            return True

    async def _release(self, gate: _AccountGate) -> None:
        async with gate.condition:
            gate.active = None
            gate.condition.notify_all()

    def _refresh_account_quotas(self, username: str, manager, account_key: str) -> None:
        endpoint = get_codebuddy_api_endpoint()
        for info in manager.get_credentials_info():
            credential_id = info.get("credential_id")
            if not credential_id:
                continue
            snapshot = manager.snapshot_credential_by_id(credential_id)
            if snapshot is None:
                continue
            try:
                related_account_key = account_key_for_credential(snapshot[0], endpoint)
            except ValueError:
                continue
            if related_account_key != account_key:
                continue
            self._quota_manager.invalidate_credential(username, credential_id)
            self._quota_manager.schedule_probe_if_running(
                username, manager, credential_id,
            )

    async def _perform_checkin(
            self,
            username: str,
            account_key: str,
            credential: Dict[str, Any],
    ) -> Dict[str, Any]:
        attempted_at = int(self._now_factory())
        bearer_token = credential.get("bearer_token")
        try:
            headers = codebuddy_api_client.generate_codebuddy_headers(
                bearer_token=bearer_token,
                user_id=credential.get("user_id"),
                account_uid=credential.get("account_uid"),
                domain=credential.get("domain"),
                enterprise_id=credential.get("enterprise_id"),
                department_full_name=credential.get("department_full_name"),
            )
            client = await self._get_http_client()
            response = await client.post(
                f"{get_codebuddy_api_endpoint()}/billing/meter/daily-checkin",
                json={},
                headers=headers,
                timeout=httpx.Timeout(30.0, connect=10.0, read=30.0),
            )
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("response is not an object")
            raw_code = body.get("code")
            code = raw_code if isinstance(raw_code, int) and not isinstance(raw_code, bool) else None
            raw_message = body.get("msg")
            message = raw_message if isinstance(raw_message, str) else "签到服务响应缺少有效消息"
            success = code == 0 or "已签到" in message
            credit = None
            checked_in_at = None
            if code == 0:
                raw_data = body.get("data")
                raw_credit = raw_data.get("credit") if isinstance(raw_data, dict) else None
                if (
                        isinstance(raw_credit, (int, float))
                        and not isinstance(raw_credit, bool)
                        and math.isfinite(raw_credit)
                ):
                    credit = float(raw_credit)
                checked_in_at = int(self._now_factory())
        except (httpx.HTTPError, TimeoutError):
            code = None
            message = "无法连接签到服务"
            credit = None
            checked_in_at = None
            success = False
        except (TypeError, ValueError):
            code = None
            message = "签到服务响应格式无效"
            credit = None
            checked_in_at = None
            success = False

        record = {
            "username": username,
            "account_key": account_key,
            "code": code,
            "message": message,
            "credit": credit,
            "attempted_at": attempted_at,
            "checked_in_at": checked_in_at,
            "success": success,
        }
        self._store.save(record)
        return self._public_detail(record)

    async def _checkin(self, source: str, username: str, manager, credential_id: str):
        if self._stopping:
            raise CredentialCheckinConflict("shutting_down")
        task = asyncio.current_task()
        self._inflight_checkins.add(task)
        try:
            return await self._checkin_once(source, username, manager, credential_id)
        finally:
            self._inflight_checkins.discard(task)

    async def _checkin_once(self, source: str, username: str, manager, credential_id: str):
        credential, account_key = self._eligible_snapshot(manager, credential_id)
        gate = self._gates.setdefault(
            self._gate_key(username, account_key), _AccountGate(),
        )
        claimed = await self._claim(gate, source)
        if not claimed:
            record = self._today_record(username, account_key)
            return self._public_detail(record) if record is not None else None
        try:
            current_credential, current_account_key = self._eligible_snapshot(
                manager, credential_id,
            )
            if current_account_key != account_key:
                raise CredentialCheckinConflict("credential_context_changed")
            existing = self._today_record(username, account_key)
            if existing is not None and existing["success"]:
                if source == "manual":
                    raise CredentialCheckinConflict("already_checked_in")
                return self._public_detail(existing)
            if source == "auto" and not self._auto_enabled_provider(username):
                return None
            result = await self._perform_checkin(
                username, account_key, current_credential,
            )
            if result["success"]:
                self._refresh_account_quotas(username, manager, account_key)
            return result
        finally:
            await self._release(gate)

    async def manual_checkin(self, username: str, manager, credential_id: str) -> Dict[str, Any]:
        return await self._checkin("manual", username, manager, credential_id)

    async def automatic_checkin(self, username: str, manager, credential_id: str):
        try:
            return await self._checkin("auto", username, manager, credential_id)
        except CredentialCheckinConflict as error:
            if str(error) == "shutting_down":
                raise
            return None

    async def run_daily_cycle(self, *, startup_compensation: bool) -> None:
        semaphore = asyncio.Semaphore(self._max_concurrency)
        jobs = []
        seen_accounts = set()
        for username in self._usernames_provider():
            if not self._auto_enabled_provider(username):
                continue
            manager = self._registry.for_username(username)
            for info in manager.get_credentials_info():
                credential_id = info.get("credential_id")
                if not credential_id or info.get("is_expired") or info.get("enterprise_id"):
                    continue
                try:
                    _credential, account_key = self._eligible_snapshot(manager, credential_id)
                except CredentialCheckinConflict:
                    continue
                scoped_key = (username, account_key)
                if scoped_key in seen_accounts:
                    continue
                seen_accounts.add(scoped_key)
                record = self._today_record(username, account_key)
                if record is not None and record["success"]:
                    continue
                if startup_compensation and record is not None and record["code"] is not None:
                    continue

                async def run(current_username=username, current_manager=manager, current_id=credential_id):
                    async with semaphore:
                        return await self.automatic_checkin(
                            current_username, current_manager, current_id,
                        )

                jobs.append(run())

        if jobs:
            results = await asyncio.gather(*jobs, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    logger.error("CodeBuddy 自动签到失败：类型=%s", type(result).__name__)

        start, end = local_day_bounds(int(self._now_factory()), self._timezone)
        try:
            self._store.delete_outside(start, end)
        except Exception:
            logger.exception("清理过期签到记录失败")

    async def startup(
            self,
            *,
            initial_scan_waiter: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        if self._task is not None:
            raise RuntimeError("credential checkin manager is already running")
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(initial_scan_waiter))

    async def shutdown(self) -> None:
        task = self._task
        stop_event = self._stop_event
        if task is None or stop_event is None:
            return
        self._stopping = True
        stop_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*tuple(self._inflight_checkins), return_exceptions=True)
        self._inflight_checkins.clear()
        self._task = None
        self._stop_event = None
        self._gates.clear()

    async def _run(
            self,
            initial_scan_waiter: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        if initial_scan_waiter is not None:
            await initial_scan_waiter()
        first_iteration = True
        while self._stop_event is not None:
            now = int(self._now_factory())
            local_date = _local_datetime(now, self._timezone).date()
            due_today = _scheduled_at(local_date, self._timezone)
            if first_iteration and now >= due_today:
                try:
                    await self.run_daily_cycle(startup_compensation=True)
                except Exception:
                    logger.exception("CodeBuddy 启动签到补偿失败")
            first_iteration = False

            now = int(self._now_factory())
            local_date = _local_datetime(now, self._timezone).date()
            target = _scheduled_at(local_date, self._timezone)
            if now >= target:
                target = _scheduled_at(local_date + timedelta(days=1), self._timezone)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=max(0, target - now),
                )
                return
            except TimeoutError:
                try:
                    await self.run_daily_cycle(startup_compensation=False)
                except Exception:
                    logger.exception("CodeBuddy 每日自动签到失败")


credential_checkin_manager = CredentialCheckinManager()
