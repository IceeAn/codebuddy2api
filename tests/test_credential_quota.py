import asyncio
import unittest
from unittest import mock

import httpx

import config
from src.codebuddy_token_manager import CodeBuddyTokenManagerRegistry
from src.credential_quota import CredentialQuotaManager, CredentialQuotaProbeError
from tests.helpers import ConfigIsolationMixin


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def quota_response(*accounts):
    return FakeResponse({
        "code": 0,
        "data": {"Response": {"Data": {"Accounts": list(accounts)}}},
    })


def enterprise_quota_response(**overrides):
    data = {
        "credit": 35.5,
        "limitNum": 100,
        "cycleStartTime": "2026-07-01 00:00:00",
        "cycleEndTime": "2026-07-31 23:59:59",
        "cycleResetTime": "2026-08-01 00:00:00",
    }
    data.update(overrides)
    return FakeResponse({"code": 0, "data": data})


def package(**overrides):
    value = {
        "Status": 0,
        "PackageName": "月度套餐",
        "CapacitySize": 100,
        "CapacityRemain": 75,
        "CapacityUsed": 25,
        "CycleStartTime": "2026-07-01 00:00:00",
        "CycleEndTime": "2026-07-31 23:59:59",
    }
    value.update(overrides)
    return value


class CredentialQuotaManagerTests(ConfigIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        config._config_cache["CODEBUDDY_API_ENDPOINT"] = "https://www.codebuddy.ai"
        config._config_cache["CODEBUDDY_ALLOWED_API_ENDPOINTS"] = "https://www.codebuddy.ai"
        self.registry = CodeBuddyTokenManagerRegistry()
        self.manager = self.registry.for_username("admin")
        self.assertTrue(self.manager.add_credential_with_data({
            "bearer_token": "secret",
            "user_id": "user-1",
            "account_uid": "account-1",
            "domain": "www.codebuddy.ai",
            "department_full_name": "研发部",
        }, "credential.json"))
        self.credential_id = self.manager.get_credentials_info()[0]["credential_id"]

    def quota_manager(self, responses, **overrides):
        client = FakeClient(responses)
        options = {
            "registry": self.registry,
            "usernames_provider": lambda: ("admin",),
            "http_client_factory": lambda: client,
            "now_factory": lambda: 1_000,
            "interval_seconds": 3_600,
        }
        options.update(overrides)
        return CredentialQuotaManager(**options), client

    async def test_probe_uses_billing_contract_and_aggregates_active_packages(self):
        manager, client = self.quota_manager([
            quota_response(
                package(),
                package(
                    PackageName="加量包",
                    CapacitySize=50.5,
                    CapacityRemain=20.25,
                    CapacityUsed=30.25,
                    CycleStartTime=None,
                    CycleEndTime=None,
                ),
                package(Status=3, CapacitySize=999, CapacityRemain=999),
            ),
        ])

        result = await manager.probe_credential("admin", self.manager, self.credential_id)

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["total"], 150.5)
        self.assertEqual(result["remaining"], 95.25)
        self.assertEqual(result["remaining_percent"], 63)
        self.assertFalse(result["estimated"])
        self.assertEqual(len(result["packages"]), 2)
        self.assertEqual(result["packages"][0]["cycle_start"], "2026-07-01 00:00:00")
        self.assertIsNone(result["packages"][1]["cycle_end"])

        url, kwargs = client.requests[0]
        self.assertEqual(url, "https://www.codebuddy.ai/v2/billing/meter/get-user-resource")
        self.assertEqual(kwargs["json"]["ProductCode"], "p_tcaca")
        self.assertEqual(kwargs["json"]["Status"], [0, 3])
        self.assertEqual(kwargs["json"]["PackageEndTimeRangeEnd"], "2127-01-01 00:00:00")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(kwargs["headers"]["X-User-Id"], "account-1")
        self.assertNotIn("X-Enterprise-Id", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["X-Department-Info"], "研发部")
        self.assertIsInstance(kwargs["timeout"], httpx.Timeout)

    async def test_enterprise_probe_uses_usage_contract_and_credit_as_used_quota(self):
        self.assertTrue(self.manager.add_credential_with_data({
            "bearer_token": "enterprise-secret",
            "user_id": "enterprise-user",
            "account_uid": "enterprise-account",
            "domain": "www.codebuddy.ai",
            "enterprise_id": "enterprise-1",
            "department_full_name": "企业研发部",
        }, "enterprise.json"))
        enterprise_id = next(
            item["credential_id"]
            for item in self.manager.get_credentials_info()
            if item.get("enterprise_id")
        )
        manager, client = self.quota_manager([enterprise_quota_response()])

        result = await manager.probe_credential("admin", self.manager, enterprise_id)

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["total"], 100)
        self.assertEqual(result["remaining"], 64.5)
        self.assertEqual(result["remaining_percent"], 64)
        self.assertEqual(result["packages"], [{
            "name": "企业额度",
            "total": 100,
            "remaining": 64.5,
            "used": 35.5,
            "cycle_start": "2026-07-01 00:00:00",
            "cycle_end": "2026-07-31 23:59:59",
        }])

        url, kwargs = client.requests[0]
        self.assertEqual(
            url,
            "https://www.codebuddy.ai/v2/billing/meter/get-enterprise-user-usage",
        )
        self.assertEqual(kwargs["json"], {})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer enterprise-secret")
        self.assertEqual(kwargs["headers"]["X-Enterprise-Id"], "enterprise-1")

    def test_enterprise_response_validation_and_integer_overage(self):
        integer = CredentialQuotaManager._parse_enterprise_response(
            enterprise_quota_response(credit=40, limitNum=100).payload
        )
        overage = CredentialQuotaManager._parse_enterprise_response(
            enterprise_quota_response(credit=120, limitNum=100).payload
        )
        self.assertEqual(integer["remaining"], 60)
        self.assertIsInstance(integer["remaining"], int)
        self.assertEqual(overage["remaining"], 0)

        invalid_bodies = (
            [],
            {"code": 9, "data": {}},
            {"code": 0, "data": []},
        )
        for body in invalid_bodies:
            with self.subTest(body=body), self.assertRaisesRegex(
                CredentialQuotaProbeError,
                "invalid_response",
            ):
                CredentialQuotaManager._parse_enterprise_response(body)

    async def test_empty_active_packages_is_real_zero_quota(self):
        manager, _client = self.quota_manager([quota_response(package(Status=3))])

        result = await manager.probe_credential("admin", self.manager, self.credential_id)

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["remaining_percent"], 0)

    async def test_null_accounts_with_zero_total_count_is_real_zero_quota(self):
        manager, _client = self.quota_manager([FakeResponse({
            "code": 0,
            "data": {
                "Response": {
                    "Data": {
                        "TotalCount": 0,
                        "TotalDosage": 0,
                        "Accounts": None,
                    },
                },
            },
        })])

        result = await manager.probe_credential("admin", self.manager, self.credential_id)

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["remaining_percent"], 0)
        self.assertEqual(result["packages"], [])

    async def test_failure_never_becomes_zero_and_preserves_stale_snapshot(self):
        manager, client = self.quota_manager([
            quota_response(package()),
            FakeResponse({"code": 9}, status_code=200),
            FakeResponse({}, status_code=503),
        ])
        await manager.probe_credential("admin", self.manager, self.credential_id)

        stale = await manager.probe_credential("admin", self.manager, self.credential_id)
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(stale["remaining"], 75)
        self.assertEqual(stale["error_type"], "invalid_response")

        other = self.registry.for_username("other")
        self.assertTrue(other.add_credential_with_data({
            "bearer_token": "other", "user_id": "other",
        }, "other.json"))
        other_id = other.get_credentials_info()[0]["credential_id"]
        failed = await manager.probe_credential("other", other, other_id)
        self.assertEqual(failed["status"], "error")
        self.assertIsNone(failed["remaining"])
        self.assertEqual(failed["error_type"], "upstream_unavailable")
        self.assertNotIn("secret", repr(manager.get_quota("admin", self.credential_id)))
        self.assertEqual(len(client.requests), 3)

    async def test_invalid_json_shape_and_numbers_fail_fast(self):
        invalid_responses = [
            FakeResponse(ValueError("bad json")),
            FakeResponse([]),
            FakeResponse({"code": 0, "data": {}}),
            quota_response({"Status": 0, "CapacitySize": True, "CapacityRemain": 1, "CapacityUsed": 0}),
            quota_response({"Status": 0, "CapacitySize": 1, "CapacityRemain": float("inf"), "CapacityUsed": 0}),
        ]
        manager, _client = self.quota_manager(invalid_responses)

        for _ in invalid_responses:
            result = await manager.probe_credential("admin", self.manager, self.credential_id)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "invalid_response")

    async def test_usage_deduction_is_atomic_clamped_and_reset_by_probe(self):
        manager, _client = self.quota_manager([
            quota_response(package()),
            quota_response(package(CapacityRemain=60, CapacityUsed=40)),
        ])
        await manager.probe_credential("admin", self.manager, self.credential_id)

        manager.apply_usage("admin", self.credential_id, 5.25, occurred_at=1_010)
        manager.apply_usage("admin", self.credential_id, 500, occurred_at=1_011)
        manager.apply_usage("admin", self.credential_id, -1, occurred_at=1_012)
        manager.apply_usage("admin", "missing", 1, occurred_at=1_013)
        estimated = manager.get_quota("admin", self.credential_id)
        self.assertEqual(estimated["remaining"], 0)
        self.assertEqual(estimated["estimated_credit_since_sync"], 505.25)
        self.assertEqual(estimated["last_estimated_at"], 1_011)
        self.assertTrue(estimated["estimated"])

        refreshed = await manager.probe_credential("admin", self.manager, self.credential_id)
        self.assertEqual(refreshed["remaining"], 60)
        self.assertEqual(refreshed["estimated_credit_since_sync"], 0)
        self.assertFalse(refreshed["estimated"])

    async def test_probe_keeps_usage_observed_while_request_is_in_flight(self):
        started = asyncio.Event()
        release = asyncio.Event()

        class DelayedClient:
            async def post(_self, _url, **_kwargs):
                started.set()
                await release.wait()
                return quota_response(package())

        manager = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=lambda: DelayedClient(),
            now_factory=lambda: 1_000,
        )
        task = asyncio.create_task(
            manager.probe_credential("admin", self.manager, self.credential_id)
        )
        await started.wait()
        manager.seed_quota_for_tests("admin", self.credential_id, total=100, remaining=80)
        manager.apply_usage("admin", self.credential_id, 4, occurred_at=1_001)
        release.set()

        result = await task

        self.assertEqual(result["remaining"], 71)
        self.assertEqual(result["estimated_credit_since_sync"], 4)
        self.assertTrue(result["estimated"])

    async def test_cache_is_user_scoped_and_invalidation_removes_value(self):
        manager, _client = self.quota_manager([quota_response(package())])
        await manager.probe_credential("admin", self.manager, self.credential_id)

        self.assertEqual(manager.get_quota("admin", self.credential_id)["status"], "fresh")
        self.assertEqual(manager.get_quota("other", self.credential_id)["status"], "unknown")
        manager.invalidate_credential("admin", self.credential_id)
        self.assertEqual(manager.get_quota("admin", self.credential_id)["status"], "unknown")

    async def test_scan_startup_interval_shutdown_and_singleflight(self):
        manager, client = self.quota_manager([
            quota_response(package()),
        ], interval_seconds=3_600)
        await manager.startup()
        for _ in range(20):
            if client.requests:
                break
            await asyncio.sleep(0)
        await manager.shutdown()
        await manager.shutdown()
        self.assertEqual(len(client.requests), 1)

        delayed_started = asyncio.Event()
        delayed_release = asyncio.Event()

        class DelayedClient:
            requests = 0

            async def post(self, _url, **_kwargs):
                self.requests += 1
                delayed_started.set()
                await delayed_release.wait()
                return quota_response(package())

        delayed_client = DelayedClient()
        singleflight = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=lambda: delayed_client,
        )
        first = asyncio.create_task(singleflight.probe_credential("admin", self.manager, self.credential_id))
        await delayed_started.wait()
        second = asyncio.create_task(singleflight.probe_credential("admin", self.manager, self.credential_id))
        delayed_release.set()
        self.assertEqual(await first, await second)
        self.assertEqual(delayed_client.requests, 1)

    async def test_transport_and_authentication_failures_are_controlled(self):
        manager, _client = self.quota_manager([
            httpx.ConnectError("private endpoint"),
            FakeResponse({}, status_code=401),
        ])
        with self.assertLogs("src.credential_quota", level="WARNING") as captured:
            transport = await manager.probe_credential("admin", self.manager, self.credential_id)
            authentication = await manager.probe_credential("admin", self.manager, self.credential_id)
        self.assertEqual(transport["error_type"], "transport_error")
        self.assertEqual(authentication["error_type"], "authentication_error")
        self.assertNotIn("private endpoint", " ".join(captured.output))

    async def test_missing_or_expired_credentials_are_not_probed(self):
        manager, client = self.quota_manager([])
        self.manager.get_credential_by_id(self.credential_id)["expires_at"] = 1

        self.assertIsNone(await manager.probe_credential("admin", self.manager, "missing"))
        self.assertIsNone(await manager.probe_credential("admin", self.manager, self.credential_id))
        await manager.scan_once()
        self.assertEqual(client.requests, [])

    async def test_schedule_probe_consumes_task_exception(self):
        manager, _client = self.quota_manager([])
        manager.probe_credential = mock.AsyncMock(side_effect=RuntimeError("boom"))
        with self.assertLogs("src.credential_quota", level="ERROR"):
            task = manager.schedule_probe("admin", self.manager, self.credential_id)
            await task

    async def test_helpers_reject_invalid_values_and_support_awaitable_client(self):
        async def client_factory():
            return "client"

        manager = CredentialQuotaManager(http_client_factory=client_factory)
        self.assertEqual(await manager._get_http_client(), "client")
        for username, credential_id in (("", "id"), ("user", "")):
            with self.subTest(username=username, credential_id=credential_id):
                with self.assertRaises(ValueError):
                    manager.get_quota(username, credential_id)

        invalid = quota_response(package(CycleStartTime=123))
        probing, _client = self.quota_manager([invalid])
        result = await probing.probe_credential("admin", self.manager, self.credential_id)
        self.assertEqual(result["error_type"], "invalid_response")

    async def test_lifecycle_rejects_duplicate_recovers_scan_failure_and_cancels_event_probe(self):
        manager, _client = self.quota_manager([], interval_seconds=0)
        scans = 0

        async def scan_once():
            nonlocal scans
            scans += 1
            if scans == 1:
                raise RuntimeError("scan")
            manager._stop_event.set()

        manager.scan_once = scan_once
        with self.assertLogs("src.credential_quota", level="ERROR"):
            await manager.startup()
            with self.assertRaises(RuntimeError):
                await manager.startup()
            await manager._task
        await manager.shutdown()
        self.assertEqual(scans, 2)

        blocking = CredentialQuotaManager(usernames_provider=lambda: ())
        release = asyncio.Event()

        async def blocked_probe(*_args):
            await release.wait()

        blocking.probe_credential = blocked_probe
        await blocking.startup()
        scheduled = blocking.schedule_probe_if_running("admin", self.manager, self.credential_id)
        self.assertIsNotNone(scheduled)
        await asyncio.sleep(0)
        await blocking.shutdown()
        self.assertTrue(scheduled.cancelled())

    async def test_scheduled_probe_propagates_cancellation(self):
        manager, _client = self.quota_manager([])
        self.assertIsNone(
            manager.schedule_probe_if_running("admin", self.manager, self.credential_id)
        )
        release = asyncio.Event()

        async def blocked_probe(*_args):
            await release.wait()

        manager.probe_credential = blocked_probe
        task = manager.schedule_probe("admin", self.manager, self.credential_id)
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        cancelled = asyncio.create_task(asyncio.sleep(1))
        manager._inflight["cancelled"] = cancelled
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        manager._remove_completed_inflight("cancelled", cancelled)
        self.assertNotIn("cancelled", manager._inflight)

    async def test_shutdown_cancels_singleflight_probe_behind_scheduled_task(self):
        manager = CredentialQuotaManager(usernames_provider=lambda: ())
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_fetch(_credential):
            started.set()
            await release.wait()

        manager._fetch_quota = blocked_fetch
        await manager.startup()
        scheduled = manager.schedule_probe("admin", self.manager, self.credential_id)
        await started.wait()

        await manager.shutdown()

        self.assertTrue(scheduled.cancelled())
        self.assertEqual(manager._inflight, {})

    async def test_unexpected_probe_error_and_invalidation_race_are_safe(self):
        manager, _client = self.quota_manager([])
        manager._fetch_quota = mock.AsyncMock(side_effect=RuntimeError("private"))
        with self.assertLogs("src.credential_quota", level="ERROR") as captured:
            failed = await manager.probe_credential("admin", self.manager, self.credential_id)
        self.assertEqual(failed["error_type"], "invalid_response")
        self.assertNotIn("private", " ".join(captured.output))

        started = asyncio.Event()
        release = asyncio.Event()

        class DelayedClient:
            async def post(_self, _url, **_kwargs):
                started.set()
                await release.wait()
                return quota_response(package())

        racing = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=lambda: DelayedClient(),
        )
        task = asyncio.create_task(racing.probe_credential("admin", self.manager, self.credential_id))
        await started.wait()
        racing.invalidate_credential("admin", self.credential_id)
        release.set()
        self.assertEqual((await task)["status"], "unknown")

    async def test_auth_header_and_additional_response_validation_failures(self):
        manager, _client = self.quota_manager([])
        with self.assertRaisesRegex(CredentialQuotaProbeError, "authentication_error"):
            await manager._fetch_quota({})
        with self.assertRaisesRegex(CredentialQuotaProbeError, "authentication_error"):
            await manager._fetch_quota({"bearer_token": "token"})

        invalid_payloads = [
            quota_response("not-an-account"),
            quota_response(package(PackageName=123)),
            quota_response(package(CapacitySize=-1)),
        ]
        validating, _client = self.quota_manager(invalid_payloads)
        for _ in invalid_payloads:
            result = await validating.probe_credential("admin", self.manager, self.credential_id)
            self.assertEqual(result["error_type"], "invalid_response")

    async def test_scan_skips_incomplete_info_and_usage_validation_paths(self):
        fake_manager = mock.Mock()
        fake_manager.get_credentials_info.return_value = [
            {},
            {"credential_id": "expired", "is_expired": True},
        ]
        registry = mock.Mock()
        registry.for_username.return_value = fake_manager
        manager = CredentialQuotaManager(
            registry=registry,
            usernames_provider=lambda: ("admin",),
        )
        await manager.scan_once()
        fake_manager.get_credential_by_id.assert_not_called()

        manager.seed_quota_for_tests("admin", "id", total=100, remaining=50)
        for value in (True, "1", float("inf"), -1):
            manager.apply_usage("admin", "id", value)
        manager.apply_usage("admin", "id", 1)
        updated = manager.get_quota("admin", "id")
        self.assertEqual(updated["remaining"], 49)
        self.assertIsInstance(updated["last_estimated_at"], int)


if __name__ == "__main__":
    unittest.main()
