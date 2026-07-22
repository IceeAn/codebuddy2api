import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import httpx
import config

from src.codebuddy_token_manager import CodeBuddyTokenManagerRegistry
from src.credential_checkin import (
    _AccountGate,
    CredentialCheckinConflict,
    CredentialCheckinManager,
    CredentialCheckinStore,
    account_key_for_credential,
    local_day_bounds,
)
from src.sqlite_database import DATABASE_FILENAME, SQLiteDatabase
from tests.helpers import ConfigIsolationMixin


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, json_error=None):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post(self, url, **kwargs):
        self.requests.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class CredentialCheckinStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / DATABASE_FILENAME
        self.store = CredentialCheckinStore(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_and_upsert_keep_one_record_per_system_user_and_account(self):
        self.store.save({
            "username": "alice",
            "account_key": "account",
            "code": 1,
            "message": "failed",
            "credit": None,
            "attempted_at": 100,
            "checked_in_at": None,
            "success": False,
        })
        self.store.save({
            "username": "alice",
            "account_key": "account",
            "code": 0,
            "message": "OK",
            "credit": 100,
            "attempted_at": 200,
            "checked_in_at": 201,
            "success": True,
        })
        self.store.save({
            "username": "bob",
            "account_key": "account",
            "code": None,
            "message": "network error",
            "credit": None,
            "attempted_at": 300,
            "checked_in_at": None,
            "success": False,
        })

        self.assertEqual(self.store.get("alice", "account"), {
            "code": 0,
            "message": "OK",
            "credit": 100.0,
            "attempted_at": 200,
            "checked_in_at": 201,
            "success": True,
        })
        self.assertIsNone(self.store.get("alice", "missing"))
        self.assertEqual(self.store.get("bob", "account")["code"], None)

        with SQLiteDatabase(self.database_path).connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM credential_daily_checkins").fetchone()[0]
        self.assertEqual(count, 2)

    def test_cleanup_deletes_every_record_outside_today(self):
        for account_key, attempted_at in (("old", 99), ("today", 150), ("future", 201)):
            self.store.save({
                "username": "alice",
                "account_key": account_key,
                "code": 1,
                "message": "failed",
                "credit": None,
                "attempted_at": attempted_at,
                "checked_in_at": None,
                "success": False,
            })

        self.assertEqual(self.store.delete_outside(100, 200), 2)
        self.assertIsNotNone(self.store.get("alice", "today"))
        self.assertIsNone(self.store.get("alice", "old"))
        self.assertIsNone(self.store.get("alice", "future"))

    def test_account_key_uses_endpoint_and_effective_x_user_id(self):
        oauth = {"account_uid": "upstream", "user_id": "fallback"}
        fallback = {"user_id": "upstream"}
        first = account_key_for_credential(oauth, "https://one.example")
        self.assertEqual(first, account_key_for_credential(fallback, "https://one.example"))
        self.assertNotEqual(first, account_key_for_credential(fallback, "https://two.example"))
        with self.assertRaises(ValueError):
            account_key_for_credential({}, "https://one.example")

    def test_local_day_bounds_use_calendar_day_across_dst(self):
        zone = ZoneInfo("America/New_York")
        now = int(datetime(2025, 3, 9, 12, tzinfo=timezone.utc).timestamp())
        start, end = local_day_bounds(now, zone)
        self.assertEqual(end - start, 23 * 3600)
        self.assertEqual(datetime.fromtimestamp(start, zone).hour, 0)
        self.assertEqual(datetime.fromtimestamp(end, zone).date().isoformat(), "2025-03-10")

        local_start, local_end = local_day_bounds(now)
        self.assertLess(local_start, local_end)


class CredentialCheckinCrossLoopLifecycleTests(unittest.TestCase):
    def test_restart_rebuilds_account_gate_after_concurrent_wait(self):
        before = int(datetime(2025, 1, 15, 9, 0, tzinfo=timezone.utc).timestamp())
        manager = CredentialCheckinManager(
            now_factory=lambda: before,
            timezone=timezone.utc,
        )
        gate_key = "alice:account"

        async def compete_once():
            await manager.startup()
            gate = manager._gates.setdefault(gate_key, _AccountGate())
            await manager._claim(gate, "auto")
            waiter = asyncio.create_task(manager._claim(gate, "manual"))
            try:
                await asyncio.sleep(0)
                if waiter.done():
                    await waiter
                self.assertTrue(gate.manual_waiter)
                await manager._release(gate)
                self.assertTrue(await waiter)
                return gate
            finally:
                if not waiter.done():
                    waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                if gate.active is not None:
                    await manager._release(gate)
                await manager.shutdown()

        first_gate = asyncio.run(compete_once())
        second_gate = asyncio.run(compete_once())

        self.assertIsNot(second_gate, first_gate)


class CredentialCheckinManagerTests(ConfigIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.registry = CodeBuddyTokenManagerRegistry()
        self.token_manager = self.registry.for_username("alice")
        self.assertTrue(self.token_manager.add_credential_with_data(
            {"bearer_token": "token", "user_id": "upstream-user"},
            "credential.json",
        ))
        self.credential_id = self.token_manager.get_credentials_info()[0]["credential_id"]
        self.database_path = config.get_database_path()
        self.store = CredentialCheckinStore(self.database_path)
        self.now = int(datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc).timestamp())
        self.zone = ZoneInfo("UTC")

    def tearDown(self):
        super().tearDown()

    def manager(self, responses, **overrides):
        client = FakeClient(responses)
        values = {
            "registry": self.registry,
            "usernames_provider": lambda: ("alice",),
            "http_client_factory": lambda: client,
            "store": self.store,
            "now_factory": lambda: self.now,
            "timezone": self.zone,
            "auto_enabled_provider": lambda _username: True,
        }
        values.update(overrides)
        return CredentialCheckinManager(**values), client

    async def test_success_ignores_http_status_and_records_credit_and_next_midnight(self):
        manager, client = self.manager([
            FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 100}}, status_code=503),
        ])
        result = await manager.manual_checkin("alice", self.token_manager, self.credential_id)

        self.assertTrue(result["success"])
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["credit"], 100.0)
        self.assertEqual(result["checked_in_at"], self.now)
        self.assertEqual(result["next_checkin_at"], self.now + 14 * 3600)
        self.assertEqual(client.requests[0]["url"], "https://copilot.tencent.com/billing/meter/daily-checkin")
        self.assertEqual(client.requests[0]["json"], {})
        self.assertEqual(client.requests[0]["headers"]["X-User-Id"], "upstream-user")

    async def test_success_refreshes_quota_for_all_credentials_of_same_account(self):
        self.assertTrue(self.token_manager.add_credential_with_data(
            {"bearer_token": "same", "account_uid": "upstream-user", "user_id": "alias"},
            "same-account.json",
        ))
        self.assertTrue(self.token_manager.add_credential_with_data(
            {"bearer_token": "other", "user_id": "other-user"},
            "other-account.json",
        ))
        related_ids = [
            info["credential_id"]
            for info in self.token_manager.get_credentials_info()
            if info.get("user_id") in {"upstream-user", "alias"}
        ]
        quota_manager = mock.Mock()
        manager, _client = self.manager(
            [FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 100}})],
            quota_manager=quota_manager,
        )

        await manager.manual_checkin("alice", self.token_manager, self.credential_id)

        self.assertEqual(
            quota_manager.invalidate_credential.call_args_list,
            [mock.call("alice", credential_id) for credential_id in related_ids],
        )
        self.assertEqual(
            quota_manager.schedule_probe_if_running.call_args_list,
            [
                mock.call("alice", self.token_manager, credential_id)
                for credential_id in related_ids
            ],
        )

    async def test_failed_checkin_does_not_refresh_quota(self):
        quota_manager = mock.Mock()
        manager, _client = self.manager(
            [FakeResponse({"code": 7, "msg": "failed"})],
            quota_manager=quota_manager,
        )

        await manager.manual_checkin("alice", self.token_manager, self.credential_id)

        quota_manager.invalidate_credential.assert_not_called()
        quota_manager.schedule_probe_if_running.assert_not_called()

    def test_quota_refresh_skips_missing_stale_and_invalid_credentials(self):
        quota_manager = mock.Mock()
        manager, _client = self.manager([], quota_manager=quota_manager)
        account_key = account_key_for_credential(
            self.token_manager.get_credential_by_id(self.credential_id),
            "https://copilot.tencent.com",
        )
        candidate_manager = mock.Mock()
        candidate_manager.get_credentials_info.return_value = [
            {},
            {"credential_id": "stale"},
            {"credential_id": "invalid"},
            {"credential_id": self.credential_id},
        ]
        candidate_manager.snapshot_credential_by_id.side_effect = {
            "stale": None,
            "invalid": ({"user_id": 123}, 0),
            self.credential_id: (
                self.token_manager.get_credential_by_id(self.credential_id),
                0,
            ),
        }.get

        manager._refresh_account_quotas("alice", candidate_manager, account_key)

        quota_manager.invalidate_credential.assert_called_once_with(
            "alice", self.credential_id,
        )
        quota_manager.schedule_probe_if_running.assert_called_once_with(
            "alice", candidate_manager, self.credential_id,
        )

    async def test_already_checked_in_message_is_success_without_credit_or_checkin_time(self):
        manager, _client = self.manager([
            FakeResponse({"code": 1001, "msg": "今天已签到，请勿重复"}, status_code=400),
        ])
        result = await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        self.assertEqual(result, {
            "code": 1001,
            "message": "今天已签到，请勿重复",
            "success": True,
            "next_checkin_at": self.now + 14 * 3600,
        })
        with self.assertRaises(CredentialCheckinConflict) as raised:
            await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        self.assertEqual(str(raised.exception), "already_checked_in")

    async def test_structured_and_unstructured_failures_are_recorded_without_retry(self):
        manager, client = self.manager([
            FakeResponse({"code": 7, "msg": "try later", "data": {"credit": 99}}),
            httpx.ConnectError("secret endpoint"),
            FakeResponse(json_error=ValueError("secret body")),
        ])
        structured = await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        transport = await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        invalid = await manager.manual_checkin("alice", self.token_manager, self.credential_id)

        self.assertEqual(structured, {"code": 7, "message": "try later", "success": False})
        self.assertEqual(transport["code"], None)
        self.assertEqual(transport["message"], "无法连接签到服务")
        self.assertEqual(invalid["message"], "签到服务响应格式无效")
        self.assertEqual(len(client.requests), 3)

    async def test_response_variants_validate_message_code_credit_and_body_shape(self):
        manager, _client = self.manager([
            FakeResponse([]),
            FakeResponse({"code": 0, "msg": None, "data": None}),
            FakeResponse({"code": 0, "msg": "OK", "data": {"credit": True}}),
            FakeResponse({"code": 0, "msg": "OK", "data": {"credit": float("nan")}}),
            FakeResponse({"msg": "已经已签到"}),
        ])
        credential = self.token_manager.get_credential_by_id(self.credential_id)
        invalid_body = await manager._perform_checkin("alice", "key", credential)
        missing_data = await manager._perform_checkin("alice", "key", credential)
        boolean_credit = await manager._perform_checkin("alice", "key", credential)
        nan_credit = await manager._perform_checkin("alice", "key", credential)
        missing_code = await manager._perform_checkin("alice", "key", credential)

        self.assertEqual(invalid_body["message"], "签到服务响应格式无效")
        self.assertEqual(missing_data["message"], "签到服务响应缺少有效消息")
        self.assertIsNone(missing_data["credit"])
        self.assertIsNone(boolean_credit["credit"])
        self.assertIsNone(nan_credit["credit"])
        self.assertTrue(missing_code["success"])
        self.assertIsNone(missing_code["code"])

    async def test_awaitable_client_factory_and_old_or_invalid_details(self):
        client = FakeClient([])

        async def factory():
            return client

        manager, _client = self.manager([], http_client_factory=factory)
        self.assertIs(await manager._get_http_client(), client)
        credential = self.token_manager.get_credential_by_id(self.credential_id)
        key = account_key_for_credential(credential, "https://copilot.tencent.com")
        self.store.save({
            "username": "alice",
            "account_key": key,
            "code": 1,
            "message": "old",
            "credit": None,
            "attempted_at": self.now - 86400,
            "checked_in_at": None,
            "success": False,
        })
        self.assertIsNone(manager.today_detail_for_credential("alice", credential))
        self.assertIsNone(manager.today_detail_for_credential("alice", None))
        self.assertIsNone(manager.today_detail_for_credential("alice", {}))
        self.assertIsNone(manager.today_detail_for_credential("alice", {"user_id": 123}))

    async def test_only_valid_personal_credentials_are_eligible(self):
        manager, client = self.manager([])
        credential = self.token_manager.get_credential_by_id(self.credential_id)
        credential["enterprise_id"] = "enterprise"
        with self.assertRaises(CredentialCheckinConflict) as enterprise:
            await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        self.assertEqual(str(enterprise.exception), "credential_ineligible")

        credential.pop("enterprise_id")
        credential["expires_at"] = 1
        with self.assertRaises(CredentialCheckinConflict):
            await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        with self.assertRaises(CredentialCheckinConflict) as missing:
            await manager.manual_checkin("alice", self.token_manager, "missing")
        self.assertEqual(str(missing.exception), "credential_not_found")
        self.assertEqual(client.requests, [])

        credential.pop("expires_at")
        credential.pop("user_id")
        with self.assertRaises(CredentialCheckinConflict) as invalid_identity:
            await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        self.assertEqual(str(invalid_identity.exception), "credential_ineligible")

    async def test_manual_waits_for_auto_and_rejects_additional_manual_request(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingClient:
            requests = 0

            async def post(_self, _url, **_kwargs):
                _self.requests += 1
                started.set()
                await release.wait()
                return FakeResponse({"code": 7, "msg": "auto failed"})

        manager, _client = self.manager([], http_client_factory=lambda: BlockingClient())
        auto = asyncio.create_task(
            manager.automatic_checkin("alice", self.token_manager, self.credential_id)
        )
        await started.wait()
        manual = asyncio.create_task(
            manager.manual_checkin("alice", self.token_manager, self.credential_id)
        )
        await asyncio.sleep(0)
        with self.assertRaises(CredentialCheckinConflict) as duplicate:
            await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        self.assertEqual(str(duplicate.exception), "manual_in_progress")

        release.set()
        await auto
        self.assertEqual((await manual)["code"], 7)

    async def test_auto_waits_for_manual_then_skips_after_success(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingClient:
            requests = 0

            async def post(_self, _url, **_kwargs):
                _self.requests += 1
                started.set()
                await release.wait()
                return FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 1}})

        client = BlockingClient()
        manager, _ = self.manager([], http_client_factory=lambda: client)
        manual = asyncio.create_task(
            manager.manual_checkin("alice", self.token_manager, self.credential_id)
        )
        await started.wait()
        automatic = asyncio.create_task(
            manager.automatic_checkin("alice", self.token_manager, self.credential_id)
        )
        release.set()
        await manual
        result = await automatic
        self.assertTrue(result["success"])
        self.assertEqual(client.requests, 1)

    async def test_disabled_auto_checkin_skips_daily_scan_but_manual_still_works(self):
        manager, client = self.manager(
            [FakeResponse({"code": 7, "msg": "manual failed"})],
            auto_enabled_provider=lambda _username: False,
        )

        await manager.run_daily_cycle(startup_compensation=False)
        self.assertEqual(client.requests, [])

        detail = await manager.manual_checkin(
            "alice", self.token_manager, self.credential_id,
        )
        self.assertEqual(detail["code"], 7)
        self.assertEqual(len(client.requests), 1)

    async def test_queued_auto_checkin_observes_switch_disabled_before_request(self):
        started = asyncio.Event()
        release = asyncio.Event()
        enabled = True

        class BlockingClient:
            def __init__(self):
                self.requests = 0

            async def post(_self, _url, **_kwargs):
                _self.requests += 1
                started.set()
                await release.wait()
                return FakeResponse({"code": 7, "msg": "manual failed"})

        client = BlockingClient()
        manager, _ = self.manager(
            [],
            http_client_factory=lambda: client,
            auto_enabled_provider=lambda _username: enabled,
        )
        manual = asyncio.create_task(manager.manual_checkin(
            "alice", self.token_manager, self.credential_id,
        ))
        await started.wait()
        automatic = asyncio.create_task(manager.automatic_checkin(
            "alice", self.token_manager, self.credential_id,
        ))
        await asyncio.sleep(0)

        enabled = False
        release.set()

        await manual
        self.assertIsNone(await automatic)
        self.assertEqual(client.requests, 1)

    async def test_disabling_switch_does_not_cancel_inflight_auto_checkin(self):
        started = asyncio.Event()
        release = asyncio.Event()
        enabled = True

        class BlockingClient:
            def __init__(self):
                self.requests = 0

            async def post(_self, _url, **_kwargs):
                _self.requests += 1
                started.set()
                await release.wait()
                return FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 1}})

        client = BlockingClient()
        manager, _ = self.manager(
            [],
            http_client_factory=lambda: client,
            auto_enabled_provider=lambda _username: enabled,
        )
        automatic = asyncio.create_task(manager.automatic_checkin(
            "alice", self.token_manager, self.credential_id,
        ))
        await started.wait()

        enabled = False
        release.set()

        detail = await automatic
        self.assertTrue(detail["success"])
        self.assertEqual(client.requests, 1)

    async def test_duplicate_auto_is_coalesced_with_and_without_existing_record(self):
        manager, _client = self.manager([])
        credential = self.token_manager.get_credential_by_id(self.credential_id)
        account_key = account_key_for_credential(credential, "https://copilot.tencent.com")
        gate = _AccountGate(active="auto")
        manager._gates[manager._gate_key("alice", account_key)] = gate
        self.assertIsNone(await manager.automatic_checkin(
            "alice", self.token_manager, self.credential_id,
        ))

        self.store.save({
            "username": "alice",
            "account_key": account_key,
            "code": 9,
            "message": "existing failure",
            "credit": None,
            "attempted_at": self.now,
            "checked_in_at": None,
            "success": False,
        })
        detail = await manager.automatic_checkin(
            "alice", self.token_manager, self.credential_id,
        )
        self.assertEqual(detail["code"], 9)

    async def test_waiting_manual_rejects_when_account_context_changes(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingClient:
            async def post(_self, _url, **_kwargs):
                started.set()
                await release.wait()
                return FakeResponse({"code": 7, "msg": "failed"})

        manager, _client = self.manager([], http_client_factory=lambda: BlockingClient())
        automatic = asyncio.create_task(manager.automatic_checkin(
            "alice", self.token_manager, self.credential_id,
        ))
        await started.wait()
        manual = asyncio.create_task(manager.manual_checkin(
            "alice", self.token_manager, self.credential_id,
        ))
        await asyncio.sleep(0)
        self.token_manager.get_credential_by_id(self.credential_id)["account_uid"] = "changed"
        release.set()
        await automatic
        with self.assertRaises(CredentialCheckinConflict) as raised:
            await manual
        self.assertEqual(str(raised.exception), "credential_context_changed")

    async def test_stopping_rejects_manual_and_propagates_from_automatic(self):
        manager, _client = self.manager([])
        manager._stopping = True
        with self.assertRaises(CredentialCheckinConflict) as manual:
            await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        with self.assertRaises(CredentialCheckinConflict) as automatic:
            await manager.automatic_checkin("alice", self.token_manager, self.credential_id)
        self.assertEqual(str(manual.exception), "shutting_down")
        self.assertEqual(str(automatic.exception), "shutting_down")

        manager._stopping = False
        self.token_manager.get_credential_by_id(self.credential_id)["enterprise_id"] = "enterprise"
        self.assertIsNone(await manager.automatic_checkin(
            "alice", self.token_manager, self.credential_id,
        ))

    async def test_same_upstream_account_across_credentials_shares_record_and_lock(self):
        self.assertTrue(self.token_manager.add_credential_with_data(
            {"bearer_token": "second", "account_uid": "upstream-user", "user_id": "other"},
            "second.json",
        ))
        second_id = self.token_manager.get_credentials_info()[1]["credential_id"]
        manager, client = self.manager([
            FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 1}}),
        ])
        await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        detail = manager.today_detail_for_credential(
            "alice", self.token_manager.get_credential_by_id(second_id)
        )
        self.assertTrue(detail["success"])
        with self.assertRaises(CredentialCheckinConflict):
            await manager.manual_checkin("alice", self.token_manager, second_id)
        self.assertEqual(len(client.requests), 1)

    async def test_daily_scan_retries_failures_but_startup_only_retries_unstructured_failures(self):
        manager, client = self.manager([
            FakeResponse({"code": 8, "msg": "manual failed"}),
            FakeResponse({"code": 8, "msg": "scheduled failed"}),
            httpx.ReadTimeout("timeout"),
            FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 1}}),
        ])
        await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        await manager.run_daily_cycle(startup_compensation=True)
        self.assertEqual(len(client.requests), 1)
        await manager.run_daily_cycle(startup_compensation=False)
        self.assertEqual(len(client.requests), 2)

        await manager.manual_checkin("alice", self.token_manager, self.credential_id)
        await manager.run_daily_cycle(startup_compensation=True)
        self.assertEqual(len(client.requests), 4)
        self.assertTrue(manager.today_detail_for_credential(
            "alice", self.token_manager.get_credential_by_id(self.credential_id)
        )["success"])

    async def test_daily_cycle_cleans_old_records_after_attempts(self):
        old = self.now - 86400
        self.store.save({
            "username": "orphan",
            "account_key": "old",
            "code": 1,
            "message": "old",
            "credit": None,
            "attempted_at": old,
            "checked_in_at": None,
            "success": False,
        })
        manager, _client = self.manager([
            FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 1}}),
        ])
        await manager.run_daily_cycle(startup_compensation=False)
        self.assertIsNone(self.store.get("orphan", "old"))

    async def test_daily_cycle_deduplicates_accounts_and_skips_success(self):
        self.assertTrue(self.token_manager.add_credential_with_data(
            {"bearer_token": "second", "account_uid": "upstream-user", "user_id": "other"},
            "second.json",
        ))
        manager, client = self.manager([
            FakeResponse({"code": 0, "msg": "OK", "data": {"credit": 1}}),
        ])
        await manager.run_daily_cycle(startup_compensation=False)
        await manager.run_daily_cycle(startup_compensation=False)
        self.assertEqual(len(client.requests), 1)

    async def test_daily_cycle_skips_invalid_infos_and_logs_job_and_cleanup_failures(self):
        fake_manager = mock.Mock()
        fake_manager.get_credentials_info.return_value = [
            {},
            {"credential_id": "expired", "is_expired": True},
            {"credential_id": "enterprise", "enterprise_id": "enterprise"},
            {"credential_id": "missing"},
            {"credential_id": self.credential_id},
        ]
        fake_manager.snapshot_credential_by_id.side_effect = lambda credential_id: (
            None if credential_id == "missing"
            else self.token_manager.snapshot_credential_by_id(credential_id)
        )
        fake_manager.is_token_expired.side_effect = self.token_manager.is_token_expired
        registry = mock.Mock()
        registry.for_username.return_value = fake_manager
        manager, _client = self.manager([], registry=registry)
        manager.automatic_checkin = mock.AsyncMock(side_effect=RuntimeError("job failed"))
        manager._store.delete_outside = mock.Mock(side_effect=RuntimeError("cleanup failed"))
        with self.assertLogs("src.credential_checkin", level="ERROR") as captured:
            await manager.run_daily_cycle(startup_compensation=False)
        output = " ".join(captured.output)
        self.assertIn("自动签到失败", output)
        self.assertIn("清理过期签到记录失败", output)

    async def test_lifecycle_waits_until_930_and_shutdown_is_idempotent(self):
        before = int(datetime(2025, 1, 15, 9, 0, tzinfo=timezone.utc).timestamp())
        manager, client = self.manager([], now_factory=lambda: before)
        manager.run_daily_cycle = mock.AsyncMock()
        await manager.startup()
        await asyncio.sleep(0)
        manager.run_daily_cycle.assert_not_awaited()
        with self.assertRaises(RuntimeError):
            await manager.startup()
        await manager.shutdown()
        await manager.shutdown()
        self.assertEqual(client.requests, [])

    async def test_shutdown_waits_for_public_checkin_before_clearing_gates(self):
        before = int(datetime(2025, 1, 15, 9, 0, tzinfo=timezone.utc).timestamp())
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingClient:
            async def post(_self, _url, **_kwargs):
                started.set()
                await release.wait()
                return FakeResponse({"code": 7, "msg": "failed"})

        manager, _client = self.manager(
            [],
            http_client_factory=BlockingClient,
            now_factory=lambda: before,
        )
        await manager.startup()
        checkin = asyncio.create_task(manager.manual_checkin(
            "alice", self.token_manager, self.credential_id,
        ))
        await started.wait()
        shutdown = asyncio.create_task(manager.shutdown())
        for _ in range(10):
            if shutdown.done():
                break
            await asyncio.sleep(0)
        try:
            self.assertFalse(shutdown.done())
            with self.assertRaises(RuntimeError):
                await manager.startup()
        finally:
            release.set()
            await checkin
            await shutdown

        await manager.startup()
        await manager.shutdown()

    async def test_lifecycle_runs_startup_compensation_after_930(self):
        after = int(datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc).timestamp())
        manager, _client = self.manager([], now_factory=lambda: after)
        manager.run_daily_cycle = mock.AsyncMock()
        await manager.startup()
        await asyncio.sleep(0)
        manager.run_daily_cycle.assert_awaited_once_with(startup_compensation=True)
        await manager.shutdown()

    async def test_startup_is_nonblocking_while_compensation_waits_for_refresh(self):
        after = int(datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc).timestamp())
        manager, _client = self.manager([], now_factory=lambda: after)
        refresh_complete = asyncio.Event()
        compensation_complete = asyncio.Event()

        async def run_daily_cycle(*, startup_compensation):
            self.assertTrue(startup_compensation)
            compensation_complete.set()

        manager.run_daily_cycle = mock.AsyncMock(side_effect=run_daily_cycle)
        await asyncio.wait_for(
            manager.startup(initial_scan_waiter=refresh_complete.wait),
            timeout=0.1,
        )
        await asyncio.sleep(0)
        manager.run_daily_cycle.assert_not_awaited()

        refresh_complete.set()
        await asyncio.wait_for(compensation_complete.wait(), timeout=0.1)
        manager.run_daily_cycle.assert_awaited_once_with(startup_compensation=True)
        await manager.shutdown()

    async def test_scheduler_logs_startup_and_periodic_failures_and_exits(self):
        after = int(datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc).timestamp())
        manager, _client = self.manager([], now_factory=lambda: after)
        manager._stop_event = asyncio.Event()
        manager.run_daily_cycle = mock.AsyncMock(side_effect=[
            RuntimeError("startup"),
            RuntimeError("periodic"),
        ])
        waits = 0

        async def immediate_wait(awaitable, *, timeout):
            del timeout
            nonlocal waits
            waits += 1
            awaitable.close()
            if waits == 1:
                raise TimeoutError
            manager._stop_event.set()
            return True

        with (
            self.assertLogs("src.credential_checkin", level="ERROR") as captured,
            mock.patch("src.credential_checkin.asyncio.wait_for", side_effect=immediate_wait),
        ):
            await manager._run()
        self.assertIn("启动签到补偿失败", " ".join(captured.output))
        self.assertIn("每日自动签到失败", " ".join(captured.output))

        manager._stop_event = None
        await manager._run()

    async def test_scheduler_runs_periodic_cycle_after_timeout(self):
        before = int(datetime(2025, 1, 15, 9, 0, tzinfo=timezone.utc).timestamp())
        manager, _client = self.manager([], now_factory=lambda: before)
        manager._stop_event = asyncio.Event()

        async def periodic(*, startup_compensation):
            self.assertFalse(startup_compensation)
            manager._stop_event.set()

        manager.run_daily_cycle = periodic
        waits = 0

        async def immediate_wait(awaitable, *, timeout):
            del timeout
            nonlocal waits
            waits += 1
            awaitable.close()
            if waits == 1:
                raise TimeoutError
            return True

        with mock.patch("src.credential_checkin.asyncio.wait_for", side_effect=immediate_wait):
            await manager._run()
