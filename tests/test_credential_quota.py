import asyncio
import unittest
from unittest import mock

import httpx

import config
from src.background_request_pacer import BackgroundRequestPacer
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
        "data": {"Response": {"Data": {
            "TotalCount": len(accounts),
            "Accounts": list(accounts),
        }}},
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
    for suffix in ("Size", "Remain", "Used"):
        cycle_key = f"CycleCapacity{suffix}"
        precise_key = f"{cycle_key}Precise"
        source = value[f"Capacity{suffix}"]
        value.setdefault(cycle_key, source)
        value.setdefault(precise_key, str(source))
    return value


class _FakeMonotonicClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


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
        self.assertTrue(result["quota_available"])
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
        self.assertEqual(
            kwargs["headers"]["X-Department-Info"],
            "%E7%A0%94%E5%8F%91%E9%83%A8",
        )
        self.assertIsInstance(kwargs["timeout"], httpx.Timeout)

    async def test_personal_probe_uses_precise_current_cycle_values(self):
        manager, _client = self.quota_manager([
            quota_response(package(
                CapacitySize=500,
                CapacityRemain=500,
                CapacityUsed=0,
                CycleCapacitySize=500,
                CycleCapacityRemain=0,
                CycleCapacityUsed=500,
                CycleCapacitySizePrecise="500",
                CycleCapacityRemainPrecise="0",
                CycleCapacityUsedPrecise="500",
            ), package(
                CapacitySize=3000,
                CapacityRemain=1776,
                CapacityUsed=1224,
                CycleCapacitySize=3000,
                CycleCapacityRemain=1776,
                CycleCapacityUsed=None,
                CycleCapacitySizePrecise="3000",
                CycleCapacityRemainPrecise="1776.80000041",
                CycleCapacityUsedPrecise="1223.19999959",
            )),
        ])

        result = await manager.probe_credential("admin", self.manager, self.credential_id)

        self.assertEqual(result["quota_type"], "personal")
        self.assertEqual(result["total"], 3500.0)
        self.assertEqual(result["remaining"], 1776.80000041)
        self.assertEqual(result["packages"][0]["remaining"], 0.0)
        self.assertEqual(result["packages"][0]["used"], 500.0)
        self.assertEqual(result["packages"][1]["used"], 1223.19999959)

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
        self.assertEqual(result["quota_type"], "enterprise")
        self.assertTrue(result["quota_available"])
        self.assertIsInstance(result["total"], float)
        self.assertIsInstance(result["packages"][0]["used"], float)
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

    async def test_manual_enterprise_quota_probe_mode_only_changes_quota_endpoint(self):
        self.assertTrue(self.manager.add_credential_with_data({
            "bearer_token": "manual-enterprise-secret",
            "user_id": "manual-enterprise-user",
            "account_uid": "manual-enterprise-account",
            "quota_probe_mode": "enterprise",
            "auth_source": "manual",
        }, "manual-enterprise.json"))
        credential_id = next(
            item["credential_id"]
            for item in self.manager.get_credentials_info()
            if item.get("quota_probe_mode") == "enterprise"
        )
        manager, client = self.quota_manager([enterprise_quota_response()])

        result = await manager.probe_credential("admin", self.manager, credential_id)

        self.assertEqual(result["quota_type"], "enterprise")
        url, kwargs = client.requests[0]
        self.assertTrue(url.endswith("/v2/billing/meter/get-enterprise-user-usage"))
        self.assertNotIn("X-Enterprise-Id", kwargs["headers"])
        self.assertNotIn("X-Tenant-Id", kwargs["headers"])
        stored = self.manager.get_credential_by_id(credential_id)
        self.assertNotIn("enterprise_id", stored)
        self.assertEqual(stored["quota_probe_mode"], "enterprise")

    async def test_candidate_probe_does_not_publish_until_explicitly_committed(self):
        manager, _client = self.quota_manager([enterprise_quota_response()])
        credential = dict(self.manager.get_credential_by_id(self.credential_id))
        credential["quota_probe_mode"] = "enterprise"

        result = await manager.probe_candidate(credential)

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["quota_type"], "enterprise")
        self.assertEqual(manager.get_quota("admin", self.credential_id)["status"], "unknown")

        manager.publish_probe_result("admin", self.credential_id, result)
        self.assertEqual(manager.get_quota("admin", self.credential_id), result)
        result["remaining"] = -1
        self.assertEqual(manager.get_quota("admin", self.credential_id)["remaining"], 64.5)

    async def test_candidate_probe_normalizes_known_transport_failure(self):
        manager, _client = self.quota_manager([httpx.ConnectError("private")])

        with self.assertRaisesRegex(CredentialQuotaProbeError, "transport_error"):
            await manager.probe_candidate(self.manager.get_credential_by_id(self.credential_id))

    async def test_candidate_probe_preserves_normalized_probe_failure(self):
        manager, _client = self.quota_manager([])
        failure = CredentialQuotaProbeError("invalid_response")

        with (
            mock.patch.object(manager, "_fetch_quota", new=mock.AsyncMock(side_effect=failure)),
            self.assertRaises(CredentialQuotaProbeError) as raised,
        ):
            await manager.probe_candidate(self.manager.get_credential_by_id(self.credential_id))

        self.assertIs(raised.exception, failure)

    def test_enterprise_response_validation_and_integer_overage(self):
        integer = CredentialQuotaManager._parse_enterprise_response(
            enterprise_quota_response(credit=40, limitNum=100).payload
        )
        overage = CredentialQuotaManager._parse_enterprise_response(
            enterprise_quota_response(credit=120, limitNum=100).payload
        )
        self.assertEqual(integer["remaining"], 60)
        self.assertIsInstance(integer["remaining"], float)
        self.assertEqual(overage["remaining"], 0)

        invalid_bodies = (
            [],
            {"code": 9, "data": {}},
            {"code": 0, "data": []},
            enterprise_quota_response(credit="1e309").payload,
            enterprise_quota_response(limitNum="1e309").payload,
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
        self.assertTrue(result["quota_available"])

    async def test_null_accounts_is_successful_without_personal_quota(self):
        manager, _client = self.quota_manager([
            FakeResponse({
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
            }),
            FakeResponse({}, status_code=503),
        ])

        result = await manager.probe_credential("admin", self.manager, self.credential_id)

        self.assertEqual(result["status"], "fresh")
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(result["remaining_percent"], 0)
        self.assertEqual(result["packages"], [])
        self.assertFalse(result["quota_available"])

        stale = await manager.probe_credential("admin", self.manager, self.credential_id)
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(stale["quota_available"])
        self.assertEqual(stale["error_type"], "upstream_unavailable")

    async def test_empty_accounts_is_successful_without_personal_quota(self):
        empty_responses = (
            {"Accounts": []},
            {"TotalCount": 0, "Accounts": []},
            {"TotalCount": 3, "Accounts": []},
            {"TotalCount": 3, "Accounts": None},
        )
        manager, _client = self.quota_manager([
            FakeResponse({"code": 0, "data": {"Response": {"Data": data}}})
            for data in empty_responses
        ])

        for _ in empty_responses:
            result = await manager.probe_credential("admin", self.manager, self.credential_id)
            self.assertEqual(result["status"], "fresh")
            self.assertEqual(result["quota_type"], "personal")
            self.assertFalse(result["quota_available"])
            self.assertEqual(result["total"], 0)
            self.assertEqual(result["remaining"], 0)
            self.assertIsNone(result["error_type"])

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
            FakeResponse({"code": 0, "data": {"Response": {"Data": {}}}}),
            FakeResponse({
                "code": 0,
                "data": {"Response": {"Data": {"Accounts": "invalid"}}},
            }),
            quota_response({"Status": 0, "CapacitySize": True, "CapacityRemain": 1, "CapacityUsed": 0}),
            quota_response({"Status": 0, "CapacitySize": 1, "CapacityRemain": float("inf"), "CapacityUsed": 0}),
        ]
        manager, _client = self.quota_manager(invalid_responses)

        for _ in invalid_responses:
            result = await manager.probe_credential("admin", self.manager, self.credential_id)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error_type"], "invalid_response")

        for precise in (True, "", " invalid", "invalid"):
            with self.subTest(precise=precise), self.assertRaisesRegex(
                CredentialQuotaProbeError,
                "invalid_response",
            ):
                CredentialQuotaManager._parse_response(
                    quota_response(package(CycleCapacitySizePrecise=precise)).payload
                )

        for field in (
                "CycleCapacitySizePrecise",
                "CycleCapacityRemainPrecise",
                "CycleCapacityUsedPrecise",
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                CredentialQuotaProbeError,
                "invalid_response",
            ):
                CredentialQuotaManager._parse_response(
                    quota_response(package(**{field: "1e309"})).payload
                )

        with self.assertRaisesRegex(CredentialQuotaProbeError, "invalid_response"):
            CredentialQuotaManager._parse_response(quota_response(
                package(
                    CycleCapacitySizePrecise="1e308",
                    CycleCapacityRemainPrecise="1e308",
                ),
                package(
                    CycleCapacitySizePrecise="1e308",
                    CycleCapacityRemainPrecise="1e308",
                ),
            ).payload)

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
        unknown = manager.get_quota("other", self.credential_id)
        self.assertEqual(unknown["status"], "unknown")
        self.assertIsNone(unknown["quota_available"])
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

    async def test_startup_scan_bypasses_background_pacing(self):
        self.assertTrue(self.manager.add_credential_with_data({
            "bearer_token": "second-secret",
            "user_id": "user-2",
            "account_uid": "account-2",
            "domain": "www.codebuddy.ai",
        }, "second.json"))
        clock = _FakeMonotonicClock()
        starts = []
        both_started = asyncio.Event()

        class TimedClient:
            async def post(_self, _url, **_kwargs):
                starts.append(clock.now)
                if len(starts) == 2:
                    both_started.set()
                return quota_response(package())

        manager = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=TimedClient,
            now_factory=lambda: 1_000,
            background_pacer=BackgroundRequestPacer(
                5,
                5,
                uniform_factory=lambda _minimum, _maximum: 5.0,
                monotonic_factory=clock.monotonic,
                sleep=clock.sleep,
            ),
        )

        await manager.startup()
        try:
            await asyncio.wait_for(both_started.wait(), timeout=0.1)
        finally:
            await manager.shutdown()

        self.assertEqual(starts, [0.0, 0.0])
        self.assertEqual(clock.sleeps, [])

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
        pacing_modes = []

        async def scan_once(*, apply_background_pacing):
            nonlocal scans
            scans += 1
            pacing_modes.append(apply_background_pacing)
            if scans == 1:
                raise RuntimeError("scan")
            manager._stop_event.set()

        manager.scan_once = scan_once
        with (
            self.assertLogs("src.credential_quota", level="ERROR"),
            mock.patch(
                "src.credential_quota.TimeoutError",
                new=type("DifferentBuiltinTimeout", (Exception,), {}),
                create=True,
            ),
        ):
            await manager.startup()
            with self.assertRaises(RuntimeError):
                await manager.startup()
            await manager._task
        await manager.shutdown()
        self.assertEqual(scans, 2)
        self.assertEqual(pacing_modes, [False, True])

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

        failure_started = asyncio.Event()
        failure_release = asyncio.Event()

        async def delayed_failure(_credential):
            failure_started.set()
            await failure_release.wait()
            raise CredentialQuotaProbeError("transport_error")

        racing._fetch_quota = delayed_failure
        failed_task = asyncio.create_task(
            racing.probe_credential("admin", self.manager, self.credential_id)
        )
        await failure_started.wait()
        racing.invalidate_credential("admin", self.credential_id)
        racing.seed_quota_for_tests("admin", self.credential_id, total=100, remaining=80)
        failure_release.set()

        self.assertEqual((await failed_task)["status"], "fresh")
        self.assertEqual(
            racing.get_quota("admin", self.credential_id)["error_type"],
            None,
        )

    def test_usage_from_invalidated_credential_generation_is_ignored(self):
        manager, _client = self.quota_manager([])
        manager.seed_quota_for_tests("admin", self.credential_id, total=100, remaining=80)
        generation = self.manager.get_quota_generation_by_id(self.credential_id)

        manager.apply_usage(
            "admin",
            self.credential_id,
            1,
            credential_generation=generation,
            occurred_at=999,
        )
        self.assertEqual(manager.get_quota("admin", self.credential_id)["remaining"], 79)

        self.manager.bump_quota_generation(self.credential_id)
        manager.apply_usage(
            "admin",
            self.credential_id,
            7,
            credential_generation=generation,
            occurred_at=1_000,
        )

        self.assertEqual(manager.get_quota("admin", self.credential_id)["remaining"], 79)

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

    async def test_background_scans_share_pacing_across_users_and_cycles_but_events_bypass_it(self):
        bob_manager = self.registry.for_username("bob")
        self.assertTrue(bob_manager.add_credential_with_data({
            "bearer_token": "bob-secret",
            "user_id": "bob-user",
            "account_uid": "bob-account",
            "domain": "www.codebuddy.ai",
        }, "bob.json"))
        clock = _FakeMonotonicClock()
        starts = []

        class TimedClient:
            async def post(_self, _url, **_kwargs):
                starts.append(clock.now)
                return quota_response(package())

        pacer = BackgroundRequestPacer(
            5,
            5,
            uniform_factory=lambda _minimum, _maximum: 5.0,
            monotonic_factory=clock.monotonic,
            sleep=clock.sleep,
        )
        manager = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin", "bob"),
            http_client_factory=TimedClient,
            now_factory=lambda: 1_000,
            background_pacer=pacer,
        )

        await manager.scan_once()
        await manager.scan_once()
        await manager.probe_credential("admin", self.manager, self.credential_id)

        self.assertEqual(starts, [0.0, 5.0, 10.0, 15.0, 15.0])
        self.assertEqual(clock.sleeps, [5.0, 5.0, 5.0])

    async def test_background_singleflight_join_does_not_block_other_probe_turns(self):
        self.assertTrue(self.manager.add_credential_with_data({
            "bearer_token": "second-secret",
            "user_id": "user-2",
            "account_uid": "account-2",
            "domain": "www.codebuddy.ai",
        }, "second.json"))
        second_id = self.manager.get_credentials_info()[1]["credential_id"]
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        release_first = asyncio.Event()

        class OverlappingClient:
            async def post(_self, _url, **kwargs):
                authorization = kwargs["headers"]["Authorization"]
                if authorization == "Bearer secret":
                    first_started.set()
                    await release_first.wait()
                else:
                    second_started.set()
                return quota_response(package())

        manager = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=OverlappingClient,
            now_factory=lambda: 1_000,
            background_pacer=BackgroundRequestPacer(0, 0),
        )
        immediate = asyncio.create_task(
            manager.probe_credential("admin", self.manager, self.credential_id),
        )
        scan = None
        await first_started.wait()
        try:
            scan = asyncio.create_task(manager.scan_once())
            await asyncio.wait_for(second_started.wait(), timeout=0.1)
            self.assertFalse(immediate.done())
        finally:
            release_first.set()
            tasks = [immediate]
            if scan is not None:
                tasks.append(scan)
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(
            manager.get_quota("admin", second_id)["status"],
            "fresh",
        )

    async def test_probe_started_while_waiting_for_turn_is_joined_outside_pacer(self):
        now = 0.0
        pacing_wait_started = asyncio.Event()
        release_pacing_wait = asyncio.Event()
        request_started = asyncio.Event()
        release_request = asyncio.Event()

        async def blocking_sleep(delay):
            self.assertEqual(delay, 10.0)
            pacing_wait_started.set()
            await release_pacing_wait.wait()

        pacer = BackgroundRequestPacer(
            10,
            10,
            uniform_factory=lambda _minimum, _maximum: 10.0,
            monotonic_factory=lambda: now,
            sleep=blocking_sleep,
        )
        async with pacer.turn() as mark_started:
            mark_started()

        class BlockingClient:
            async def post(_self, _url, **_kwargs):
                request_started.set()
                await release_request.wait()
                return quota_response(package())

        manager = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=BlockingClient,
            now_factory=lambda: 1_000,
            background_pacer=pacer,
        )
        scan = asyncio.create_task(manager.scan_once())
        immediate = None
        follower = None
        await pacing_wait_started.wait()
        try:
            immediate = asyncio.create_task(manager.probe_credential(
                "admin",
                self.manager,
                self.credential_id,
            ))
            await request_started.wait()
            now = 10.0
            release_pacing_wait.set()
            turn_acquired = asyncio.Event()

            async def acquire_followup_turn():
                async with pacer.turn():
                    turn_acquired.set()

            follower = asyncio.create_task(acquire_followup_turn())
            await asyncio.wait_for(turn_acquired.wait(), timeout=0.1)
            self.assertFalse(scan.done())
            self.assertFalse(immediate.done())
        finally:
            release_pacing_wait.set()
            release_request.set()
            tasks = [scan]
            if immediate is not None:
                tasks.append(immediate)
            if follower is not None:
                tasks.append(follower)
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_locally_invalid_background_probe_does_not_consume_interval(self):
        self.assertTrue(self.manager.add_credential_with_data({
            "bearer_token": "valid-secret",
            "user_id": "user-2",
            "account_uid": "account-2",
            "domain": "www.codebuddy.ai",
        }, "valid.json"))
        self.manager.get_credential_by_id(
            self.credential_id,
        )["department_full_name"] = "invalid\nheader"
        clock = _FakeMonotonicClock()
        starts = []

        class TimedClient:
            async def post(_self, _url, **_kwargs):
                starts.append(clock.now)
                return quota_response(package())

        manager = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=TimedClient,
            now_factory=lambda: 1_000,
            background_pacer=BackgroundRequestPacer(
                5,
                5,
                uniform_factory=lambda _minimum, _maximum: 5.0,
                monotonic_factory=clock.monotonic,
                sleep=clock.sleep,
            ),
        )

        await manager.scan_once()

        self.assertEqual(starts, [0.0])
        self.assertEqual(clock.sleeps, [])

    async def test_background_start_is_marked_after_client_preparation_before_post(self):
        events = []

        class OrderedClient:
            async def post(_self, _url, **_kwargs):
                events.append("post")
                return quota_response(package())

        async def client_factory():
            events.append("client")
            return OrderedClient()

        manager = CredentialQuotaManager(http_client_factory=client_factory)
        credential = self.manager.get_credential_by_id(self.credential_id)

        await manager._fetch_quota(
            credential,
            _mark_started=lambda: events.append("mark"),
        )

        self.assertEqual(events, ["client", "mark", "post"])

    async def test_cancelled_scan_keeps_turn_until_shielded_probe_starts(self):
        client_factory_started = asyncio.Event()
        release_client_factory = asyncio.Event()
        request_started = asyncio.Event()

        class OrderedClient:
            async def post(_self, _url, **_kwargs):
                request_started.set()
                return quota_response(package())

        async def client_factory():
            client_factory_started.set()
            await release_client_factory.wait()
            return OrderedClient()

        pacer = BackgroundRequestPacer(0, 0)
        manager = CredentialQuotaManager(
            registry=self.registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=client_factory,
            now_factory=lambda: 1_000,
            background_pacer=pacer,
        )
        scan = asyncio.create_task(manager.scan_once())
        await client_factory_started.wait()
        probe = next(iter(manager._inflight.values()))

        scan.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await scan

        next_turn_entered = asyncio.Event()

        async def take_next_turn():
            async with pacer.turn():
                next_turn_entered.set()

        next_turn = asyncio.create_task(take_next_turn())
        await asyncio.sleep(0)
        entered_before_probe_start = next_turn_entered.is_set()

        release_client_factory.set()
        result, _ = await asyncio.gather(probe, next_turn)

        self.assertFalse(entered_before_probe_start)
        self.assertTrue(request_started.is_set())
        self.assertEqual(result["status"], "fresh")

    async def test_scan_period_is_measured_from_cycle_start(self):
        manager = CredentialQuotaManager(
            usernames_provider=lambda: (),
            interval_seconds=3_600,
            monotonic_factory=mock.Mock(side_effect=[100.0, 700.0]),
        )
        manager._stop_event = asyncio.Event()
        timeout_value = None

        async def stop_during_wait(awaitable, *, timeout):
            nonlocal timeout_value
            timeout_value = timeout
            awaitable.close()
            manager._stop_event.set()
            return True

        with mock.patch("src.credential_quota.asyncio.wait_for", side_effect=stop_during_wait):
            await manager._run()

        self.assertEqual(timeout_value, 3_000.0)

    async def test_scan_longer_than_interval_starts_next_cycle_immediately(self):
        manager = CredentialQuotaManager(
            usernames_provider=lambda: (),
            interval_seconds=3_600,
            monotonic_factory=mock.Mock(side_effect=[0.0, 3_601.0, 3_601.0]),
        )
        manager._stop_event = asyncio.Event()
        scans = 0
        pacing_modes = []

        async def scan_once(*, apply_background_pacing):
            nonlocal scans
            scans += 1
            pacing_modes.append(apply_background_pacing)
            if scans == 2:
                manager._stop_event.set()

        manager.scan_once = scan_once
        with mock.patch("src.credential_quota.asyncio.wait_for") as wait_for:
            await manager._run()

        self.assertEqual(scans, 2)
        self.assertEqual(pacing_modes, [False, True])
        wait_for.assert_not_called()

    async def test_scan_timeout_starts_next_cycle_and_uses_asyncio_timeout_error(self):
        manager = CredentialQuotaManager(
            usernames_provider=lambda: (),
            interval_seconds=3_600,
            monotonic_factory=mock.Mock(side_effect=[100.0, 700.0, 700.0]),
        )
        manager._stop_event = asyncio.Event()
        scans = 0
        pacing_modes = []

        async def scan_once(*, apply_background_pacing):
            nonlocal scans
            scans += 1
            pacing_modes.append(apply_background_pacing)
            if scans == 2:
                manager._stop_event.set()

        async def expire_wait(awaitable, *, timeout):
            self.assertEqual(timeout, 3_000.0)
            awaitable.close()
            raise asyncio.TimeoutError

        manager.scan_once = scan_once
        with (
            mock.patch("src.credential_quota.asyncio.wait_for", side_effect=expire_wait),
            mock.patch(
                "src.credential_quota.TimeoutError",
                new=type("DifferentBuiltinTimeout", (Exception,), {}),
                create=True,
            ),
        ):
            await manager._run()

        self.assertEqual(scans, 2)
        self.assertEqual(pacing_modes, [False, True])

    async def test_shutdown_cancels_scan_waiting_for_background_interval(self):
        self.assertTrue(self.manager.add_credential_with_data({
            "bearer_token": "second-secret",
            "user_id": "user-2",
            "account_uid": "account-2",
            "domain": "www.codebuddy.ai",
        }, "second.json"))
        sleep_started = asyncio.Event()
        release_sleep = asyncio.Event()

        async def blocking_sleep(_delay):
            sleep_started.set()
            await release_sleep.wait()

        pacer = BackgroundRequestPacer(
            10,
            10,
            uniform_factory=lambda _minimum, _maximum: 10.0,
            monotonic_factory=lambda: 0.0,
            sleep=blocking_sleep,
        )
        manager, client = self.quota_manager(
            [
                quota_response(package()),
                quota_response(package()),
                quota_response(package()),
            ],
            background_pacer=pacer,
            interval_seconds=0,
        )
        await manager.startup()
        await sleep_started.wait()

        await asyncio.wait_for(manager.shutdown(), timeout=0.1)

        self.assertEqual(len(client.requests), 3)
        self.assertIsNone(manager._task)


if __name__ == "__main__":
    unittest.main()
