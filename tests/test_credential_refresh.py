import tempfile
import unittest
from copy import deepcopy
from unittest import mock

import asyncio
import httpx

import config
from src.codebuddy_token_manager import CodeBuddyTokenManager, CodeBuddyTokenManagerRegistry
from src.credential_refresh import CredentialRefreshError, CredentialRefreshManager, _RefreshState
from tests.helpers import ConfigIsolationMixin


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeManager:
    def __init__(self, snapshot=None, replace_result=True, infos=(), replace_results=None):
        self.snapshot = snapshot
        self.replace_result = replace_result
        self.replace_results = list(replace_results) if replace_results is not None else None
        self.infos = list(infos)
        self.replacements = []

    def snapshot_credential_by_id(self, _credential_id):
        return self.snapshot

    def replace_credential_by_id(
            self, credential_id, data, *, expected_generation, quota_changed=False,
    ):
        self.replacements.append((credential_id, data, expected_generation, quota_changed))
        result = (
            self.replace_results.pop(0)
            if self.replace_results is not None
            else self.replace_result
        )
        if result:
            self.snapshot = (deepcopy(data), expected_generation + 1)
        return result

    def get_credentials_info(self):
        return self.infos


class CredentialRefreshManagerTests(ConfigIsolationMixin, unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def oauth_credential(**overrides):
        value = {
            "bearer_token": "old-access",
            "refresh_token": "refresh",
            "user_id": "jwt-sub",
            "domain": "copilot.tencent.com",
            "created_at": 900_000,
            "expires_at": 1_000_000,
        }
        value.update(overrides)
        return value

    @staticmethod
    def raw_account(**overrides):
        value = {
            "uid": "account-uid",
            "type": "ultimate",
            "enterpriseId": "enterprise-1",
            "pluginEnabled": True,
            "lastLogin": True,
        }
        value.update(overrides)
        return value

    @staticmethod
    def token_response(**overrides):
        data = {
            "accessToken": "new-access",
            "refreshToken": "new-refresh",
            "expiresIn": 172800,
        }
        data.update(overrides)
        return FakeResponse({"code": 0, "data": data})

    async def test_lifecycle_runs_immediately_rejects_duplicate_and_shutdown_is_idempotent(self):
        refresher = CredentialRefreshManager(
            usernames_provider=lambda: (), interval_seconds=3600,
        )
        refresher.scan_once = mock.AsyncMock()

        await refresher.startup()
        await asyncio.sleep(0)
        refresher.scan_once.assert_awaited_once()
        with self.assertRaises(RuntimeError):
            await refresher.startup()
        await refresher.shutdown()
        await refresher.shutdown()
        await refresher._run()

    async def test_shutdown_cancels_blocked_scan_and_inflight_task(self):
        refresher = CredentialRefreshManager(usernames_provider=lambda: ())
        scan_blocker = asyncio.Event()
        inflight_blocker = asyncio.Event()
        refresher.scan_once = mock.AsyncMock(side_effect=scan_blocker.wait)
        inflight = asyncio.create_task(inflight_blocker.wait())
        refresher._inflight["admin:cred"] = inflight

        await refresher.startup()
        await asyncio.sleep(0)
        await asyncio.wait_for(refresher.shutdown(), timeout=0.1)

        self.assertTrue(inflight.cancelled())
        self.assertEqual(refresher._inflight, {})
        self.assertIsNone(refresher._task)
        self.assertIsNone(refresher._stop_event)

    async def test_switch_waiting_for_refresh_does_not_escape_shutdown(self):
        refresher = CredentialRefreshManager(usernames_provider=lambda: ())
        refresher.scan_once = mock.AsyncMock()
        await refresher.startup()
        await asyncio.sleep(0)

        release_refresh = asyncio.Event()
        existing = asyncio.create_task(release_refresh.wait())
        refresher._inflight["admin:cred"] = existing
        shutdown_tasks = []

        def start_shutdown(_task):
            shutdown_tasks.append(asyncio.create_task(refresher.shutdown()))

        existing.add_done_callback(start_shutdown)
        refresher._perform_switch = mock.AsyncMock(return_value=True)
        switch_task = asyncio.create_task(refresher.switch_account(
            "admin",
            FakeManager(snapshot=(self.oauth_credential(), 0)),
            "cred",
            "account",
        ))
        await asyncio.sleep(0)
        release_refresh.set()

        try:
            with self.assertRaisesRegex(CredentialRefreshError, "shutting_down"):
                await asyncio.wait_for(switch_task, timeout=0.1)
        finally:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)

        refresher._perform_switch.assert_not_awaited()
        self.assertEqual(refresher._inflight, {})

    async def test_run_logs_scan_failure_and_continues_after_hourly_timeout(self):
        refresher = CredentialRefreshManager(usernames_provider=lambda: (), interval_seconds=0)
        stop = asyncio.Event()
        refresher._stop_event = stop

        async def scan():
            if scan.await_count == 2:
                stop.set()

        scan.await_count = 0

        async def counted_scan():
            scan.await_count += 1
            if scan.await_count == 1:
                raise RuntimeError("scan failed")
            stop.set()

        refresher.scan_once = counted_scan
        wait_count = 0

        async def immediate_wait(awaitable, *, timeout):
            del timeout
            nonlocal wait_count
            wait_count += 1
            awaitable.close()
            if wait_count == 1:
                raise TimeoutError

        with (
            self.assertLogs("src.credential_refresh", level="ERROR"),
            mock.patch("src.credential_refresh.asyncio.wait_for", side_effect=immediate_wait),
        ):
            await refresher._run()
        self.assertEqual(scan.await_count, 2)

    async def test_awaitable_http_factory_and_expiry_fallbacks(self):
        client = FakeClient([])

        async def factory():
            return client

        refresher = CredentialRefreshManager(http_client_factory=factory, now_factory=lambda: 100)
        self.assertIs(await refresher._get_http_client(), client)
        self.assertEqual(refresher._expires_at({"created_at": 10, "expires_in": 20}), 30)
        self.assertIsNone(refresher._expires_at({"created_at": True, "expires_in": 20}))
        self.assertFalse(refresher._should_refresh({"refresh_token": "r"}))
        self.assertFalse(refresher._should_refresh({
            "refresh_token": "r", "expires_at": 100, "refresh_expires_at": 100,
        }))
        self.assertTrue(refresher._should_refresh({
            "refresh_accounts_pending": True,
            "refresh_token": "r",
        }))
        self.assertTrue(refresher._should_refresh({
            "refresh_accounts_pending": True,
            "refresh_token": "r",
            "expires_at": 101,
            "refresh_expires_at": 100,
        }))
        self.assertFalse(refresher._should_refresh({
            "refresh_accounts_pending": True,
            "refresh_token": "r",
            "expires_at": 100,
            "refresh_expires_at": 100,
        }))
        self.assertFalse(refresher._should_refresh({
            "refresh_accounts_pending": True,
            "expires_at": 100,
        }))

    async def test_scan_skips_manual_token_without_refresh_token(self):
        registry = CodeBuddyTokenManagerRegistry()
        manager = registry.for_username("admin")
        self.assertTrue(manager.add_credential("opaque.12345678"))
        client = FakeClient([])
        refresher = CredentialRefreshManager(
            registry=registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=lambda: client,
            now_factory=lambda: 1_000_000,
            retry_delays=(),
        )

        await refresher.scan_once()

        self.assertEqual(client.requests, [])

    async def test_refresh_starts_at_24_hour_window_and_preserves_credential_id(self):
        registry = CodeBuddyTokenManagerRegistry()
        manager = registry.for_username("admin")
        now = 1_000_000
        self.assertTrue(manager.add_credential_with_data({
            "credential_schema_version": 2,
            "bearer_token": "old-access",
            "refresh_token": "refresh",
            "user_id": "jwt-sub",
            "account_uid": "account-uid",
            "account_id": "current-account",
            "account_type": "ultimate",
            "enterprise_id": "enterprise-1",
            "domain": "copilot.tencent.com",
            "created_at": now - 100,
            "expires_at": now + 86400,
            "api_endpoint": "https://copilot.tencent.com",
            "site_type": "china",
            "auth_source": "oauth",
        }, "stable.json"))
        credential_id = manager.get_credentials_info()[0]["credential_id"]
        account = {
            "uid": "account-uid",
            "type": "ultimate",
            "enterpriseId": "enterprise-1",
            "pluginEnabled": True,
            "lastLogin": True,
        }
        # 使用真实算法生成当前账号 ID，避免浏览器或测试伪造企业上下文。
        from src.codebuddy_oauth import TokenParser
        manager.get_credential_by_id(credential_id)["account_id"] = TokenParser._account_id(account)
        client = FakeClient([
            FakeResponse({
                "code": 0,
                "data": {
                    "accessToken": "new-access",
                    "refreshToken": "new-refresh",
                    "tokenType": "Bearer",
                    "expiresIn": 172800,
                    "refreshExpiresIn": 604800,
                    "domain": "copilot.tencent.com",
                },
            }),
            FakeResponse({"code": 0, "data": {"accounts": [account]}}),
        ])
        refresher = CredentialRefreshManager(
            registry=registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=lambda: client,
            now_factory=lambda: now,
            retry_delays=(),
        )

        await refresher.scan_once()

        updated = manager.get_credential_by_id(credential_id)
        self.assertEqual(updated["bearer_token"], "new-access")
        self.assertEqual(updated["refresh_token"], "new-refresh")
        self.assertEqual(manager.get_credentials_info()[0]["credential_id"], credential_id)
        self.assertEqual([request[0] for request in client.requests], ["POST", "GET"])

    async def test_scan_does_not_refresh_before_24_hour_window(self):
        registry = CodeBuddyTokenManagerRegistry()
        manager = registry.for_username("admin")
        self.assertTrue(manager.add_credential_with_data({
            "bearer_token": "old",
            "refresh_token": "refresh",
            "user_id": "user",
            "created_at": 1,
            "expires_at": 1_000_000 + 86401,
        }, "stable.json"))
        client = FakeClient([])
        refresher = CredentialRefreshManager(
            registry=registry,
            usernames_provider=lambda: ("admin",),
            http_client_factory=lambda: client,
            now_factory=lambda: 1_000_000,
            retry_delays=(),
        )

        await refresher.scan_once()

        self.assertEqual(client.requests, [])

    async def test_scan_ignores_disappeared_snapshot_and_isolates_refresh_failure(self):
        manager = FakeManager(
            snapshot=None,
            infos=[{"credential_id": "gone"}],
        )
        registry = mock.Mock()
        registry.for_username.return_value = manager
        refresher = CredentialRefreshManager(registry=registry, usernames_provider=lambda: ("admin",))
        await refresher.scan_once()

        credential = self.oauth_credential()
        manager.snapshot = (credential, 0)
        refresher.refresh_credential = mock.AsyncMock(side_effect=CredentialRefreshError("failed"))
        with self.assertLogs("src.credential_refresh", level="WARNING"):
            await refresher.scan_once()

    async def test_refresh_missing_snapshot_and_singleflight(self):
        manager = FakeManager(snapshot=None)
        refresher = CredentialRefreshManager()
        self.assertFalse(await refresher.refresh_credential("admin", manager, "missing"))

        future = asyncio.create_task(asyncio.sleep(0, result=True))
        refresher._inflight["admin:cred"] = future
        self.assertTrue(await refresher.refresh_credential("admin", manager, "cred"))

    async def test_stopping_manager_rejects_new_refresh_and_account_switch(self):
        refresher = CredentialRefreshManager()
        refresher._stopping = True
        manager = FakeManager(snapshot=(self.oauth_credential(), 0))

        self.assertFalse(await refresher.refresh_credential("admin", manager, "cred"))
        with self.assertRaisesRegex(CredentialRefreshError, "shutting_down"):
            await refresher.switch_account("admin", manager, "cred", "account")

    async def test_refresh_retries_temporary_transport_errors_but_not_unauthorized(self):
        snapshot = (self.oauth_credential(), 1)
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: FakeClient([]), retry_delays=(0,),
        )
        refresher._refresh_once = mock.AsyncMock(
            side_effect=[CredentialRefreshError("temporary"), True],
        )
        self.assertTrue(await refresher._perform_refresh("admin", FakeManager(), "cred", snapshot))

        refresher._refresh_once = mock.AsyncMock(
            side_effect=[httpx.ConnectError("offline"), True],
        )
        self.assertTrue(await refresher._perform_refresh("admin", FakeManager(), "cred", snapshot))

        for error in (CredentialRefreshError("unauthorized"), httpx.ConnectError("offline")):
            refresher = CredentialRefreshManager(
                http_client_factory=lambda: FakeClient([]), retry_delays=(),
            )
            refresher._refresh_once = mock.AsyncMock(side_effect=error)
            with self.assertRaisesRegex(CredentialRefreshError, "refresh_failed"):
                await refresher._perform_refresh("admin", FakeManager(), "cred", snapshot)

        refresher = CredentialRefreshManager(
            http_client_factory=lambda: FakeClient([]), retry_delays=(0.001,),
        )
        refresher._refresh_once = mock.AsyncMock(
            side_effect=[CredentialRefreshError("temporary"), True],
        )
        with mock.patch("src.credential_refresh.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            self.assertTrue(await refresher._perform_refresh("admin", FakeManager(), "cred", snapshot))
        sleep.assert_awaited_once_with(0.001)

        refresher = CredentialRefreshManager(
            http_client_factory=lambda: FakeClient([]), retry_delays=(0,),
        )
        refresher._refresh_once = mock.AsyncMock(
            side_effect=CredentialRefreshError("refresh_unavailable"),
        )
        with self.assertRaisesRegex(CredentialRefreshError, "refresh_failed"):
            await refresher._perform_refresh("admin", FakeManager(), "cred", snapshot)
        refresher._refresh_once.assert_awaited_once()

    async def test_refresh_persists_rotated_token_and_only_retries_account_stage(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account()
        normalized = TokenParser._normalize_account(raw)
        credential = self.oauth_credential(
            account_id=normalized["account_id"],
            account_uid=normalized["uid"],
            account_type=normalized["type"],
            enterprise_id=normalized["enterprise_id"],
            accounts=[normalized],
            compatibility_data={"legacy": True},
            upstream_responses={"login_token": {"raw": True}},
        )
        manager = FakeManager(snapshot=(credential, 4))
        client = FakeClient([
            self.token_response(),
            httpx.ConnectError("accounts offline"),
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: client,
            now_factory=lambda: 1_000_000,
            retry_delays=(0,),
        )

        self.assertTrue(await refresher._perform_refresh(
            "admin", manager, "cred", (credential, 4),
        ))

        self.assertEqual([request[0] for request in client.requests], ["POST", "GET", "GET"])
        self.assertEqual(len(manager.replacements), 2)
        pending = manager.replacements[0][1]
        completed = manager.replacements[1][1]
        self.assertEqual(pending["refresh_token"], "new-refresh")
        self.assertTrue(pending["refresh_accounts_pending"])
        self.assertEqual(pending["compatibility_data"], {"legacy": True})
        self.assertEqual(pending["upstream_responses"]["login_token"], {"raw": True})
        self.assertEqual(manager.replacements[0][2], 4)
        self.assertNotIn("refresh_accounts_pending", completed)
        self.assertEqual(manager.replacements[1][2], 5)

    async def test_refresh_repairs_stale_enterprise_context_for_personal_account(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account(type="personal")
        raw.pop("enterpriseId")
        normalized = TokenParser._normalize_account(raw)
        credential = self.oauth_credential(
            account_id=normalized["account_id"],
            account_uid=normalized["uid"],
            account_type="personal",
            enterprise_id="stale-enterprise",
            accounts=[normalized],
        )
        manager = FakeManager(snapshot=(credential, 3))
        client = FakeClient([
            self.token_response(enterpriseId=None),
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: client,
            retry_delays=(),
        )

        self.assertTrue(await refresher._perform_refresh(
            "admin", manager, "cred", (credential, 3),
        ))
        updated = manager.snapshot[0]
        self.assertEqual(updated["account_id"], normalized["account_id"])
        self.assertNotIn("enterprise_id", updated)
        self.assertNotIn("enterprise_id", updated["user_info"])

    async def test_pending_account_sync_survives_retry_exhaustion_and_restart(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account()
        normalized = TokenParser._normalize_account(raw)
        credential = self.oauth_credential(
            account_id=normalized["account_id"],
            accounts=[normalized],
        )
        manager = FakeManager(snapshot=(credential, 2))
        first_client = FakeClient([
            self.token_response(),
            httpx.ConnectError("accounts offline"),
            httpx.ConnectError("accounts still offline"),
        ])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: first_client,
            retry_delays=(0,),
        )

        with self.assertRaisesRegex(CredentialRefreshError, "refresh_failed"):
            await refresher._perform_refresh("admin", manager, "cred", (credential, 2))

        pending, pending_generation = manager.snapshot
        self.assertEqual(pending["bearer_token"], "new-access")
        self.assertEqual(pending["refresh_token"], "new-refresh")
        self.assertTrue(pending["refresh_accounts_pending"])
        self.assertTrue(refresher._should_refresh(pending))
        self.assertEqual([request[0] for request in first_client.requests], ["POST", "GET", "GET"])

        second_client = FakeClient([
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        restarted = CredentialRefreshManager(
            http_client_factory=lambda: second_client,
            retry_delays=(),
        )

        self.assertTrue(await restarted._perform_refresh(
            "admin", manager, "cred", (pending, pending_generation),
        ))
        self.assertEqual([request[0] for request in second_client.requests], ["GET"])
        self.assertNotIn("refresh_accounts_pending", manager.snapshot[0])

    async def test_expired_pending_access_token_refreshes_before_account_sync(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account()
        normalized = TokenParser._normalize_account(raw)
        pending = self.oauth_credential(
            account_id=normalized["account_id"],
            account_uid=normalized["uid"],
            account_type=normalized["type"],
            enterprise_id=normalized["enterprise_id"],
            accounts=[normalized],
            expires_at=1_000_000,
            refresh_accounts_pending=True,
        )
        manager = FakeManager(snapshot=(pending, 7))
        client = FakeClient([
            self.token_response(),
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: client,
            now_factory=lambda: 1_000_000,
            retry_delays=(),
        )

        self.assertTrue(await refresher._perform_refresh(
            "admin", manager, "cred", (pending, 7),
        ))

        self.assertEqual([request[0] for request in client.requests], ["POST", "GET"])
        self.assertEqual(client.requests[0][2]["headers"]["X-Refresh-Token"], "refresh")
        self.assertEqual([replacement[2] for replacement in manager.replacements], [7, 8])
        self.assertEqual(manager.snapshot[0]["bearer_token"], "new-access")
        self.assertNotIn("refresh_accounts_pending", manager.snapshot[0])

    async def test_unauthorized_pending_account_sync_refreshes_and_retries(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account()
        normalized = TokenParser._normalize_account(raw)
        pending = self.oauth_credential(
            account_id=normalized["account_id"],
            account_uid=normalized["uid"],
            account_type=normalized["type"],
            enterprise_id=normalized["enterprise_id"],
            accounts=[normalized],
            expires_at=2_000_000,
            refresh_accounts_pending=True,
        )
        manager = FakeManager(snapshot=(pending, 3))
        client = FakeClient([
            FakeResponse({"code": 401}, status_code=401),
            self.token_response(),
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: client,
            now_factory=lambda: 1_000_000,
            retry_delays=(),
        )

        self.assertTrue(await refresher._perform_refresh(
            "admin", manager, "cred", (pending, 3),
        ))

        self.assertEqual(
            [request[0] for request in client.requests],
            ["GET", "POST", "GET"],
        )
        self.assertEqual(client.requests[1][2]["headers"]["X-Refresh-Token"], "refresh")
        self.assertEqual([replacement[2] for replacement in manager.replacements], [3, 4])
        self.assertNotIn("refresh_accounts_pending", manager.snapshot[0])

    async def test_unrefreshable_pending_account_unauthorized_stops_without_retry(self):
        pending = self.oauth_credential(refresh_accounts_pending=True)
        pending.pop("expires_at")
        cases = (
            pending,
            self.oauth_credential(
                refresh_accounts_pending=True,
                expires_at=2_000_000,
                refresh_token=None,
            ),
            self.oauth_credential(
                refresh_accounts_pending=True,
                expires_at=2_000_000,
                refresh_expires_at=1_000_000,
            ),
        )

        for credential in cases:
            with self.subTest(credential=credential):
                manager = FakeManager(snapshot=(credential, 3))
                client = FakeClient([
                    FakeResponse({"code": 401}, status_code=401),
                    FakeResponse({"code": 401}, status_code=401),
                ])
                refresher = CredentialRefreshManager(
                    http_client_factory=lambda: client,
                    now_factory=lambda: 1_000_000,
                    retry_delays=(0,),
                )

                with self.assertRaisesRegex(
                        CredentialRefreshError, "refresh_failed",
                ) as raised:
                    await refresher._perform_refresh(
                        "admin", manager, "cred", (credential, 3),
                    )

                self.assertEqual(str(raised.exception.__cause__), "unauthorized")
                self.assertEqual([request[0] for request in client.requests], ["GET"])
                self.assertEqual(manager.replacements, [])

    async def test_token_refresh_rejects_missing_or_expired_refresh_token(self):
        refresher = CredentialRefreshManager(now_factory=lambda: 1_000_000)
        cases = (
            self.oauth_credential(refresh_token=None),
            self.oauth_credential(refresh_expires_at=1_000_000),
        )

        for credential in cases:
            with self.subTest(credential=credential):
                client = FakeClient([])
                with self.assertRaisesRegex(CredentialRefreshError, "refresh_unavailable"):
                    await refresher._refresh_once(
                        client,
                        "admin",
                        FakeManager(),
                        "cred",
                        _RefreshState(credential=credential, generation=0),
                    )
                self.assertEqual(client.requests, [])

    async def test_refresh_generation_conflict_stops_each_persistence_stage(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account()
        normalized = TokenParser._normalize_account(raw)
        credential = self.oauth_credential(
            account_id=normalized["account_id"],
            accounts=[normalized],
        )
        first_manager = FakeManager(snapshot=(credential, 1), replace_result=False)
        first_client = FakeClient([self.token_response()])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: first_client,
            retry_delays=(),
        )

        self.assertFalse(await refresher._perform_refresh(
            "admin", first_manager, "cred", (credential, 1),
        ))
        self.assertEqual([request[0] for request in first_client.requests], ["POST"])

        second_manager = FakeManager(
            snapshot=(credential, 1),
            replace_results=(True, False),
        )
        second_client = FakeClient([
            self.token_response(),
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: second_client,
            retry_delays=(),
        )

        self.assertFalse(await refresher._perform_refresh(
            "admin", second_manager, "cred", (credential, 1),
        ))
        self.assertEqual(len(second_manager.replacements), 2)
        self.assertEqual(second_manager.replacements[1][2], 2)

        pending = self.oauth_credential(
            refresh_accounts_pending=True,
            expires_at=2_000_000,
        )
        third_manager = FakeManager(snapshot=(pending, 3), replace_result=False)
        third_client = FakeClient([
            FakeResponse({"code": 401}, status_code=401),
            self.token_response(),
        ])
        refresher = CredentialRefreshManager(
            http_client_factory=lambda: third_client,
            now_factory=lambda: 1_000_000,
            retry_delays=(),
        )

        self.assertFalse(await refresher._perform_refresh(
            "admin", third_manager, "cred", (pending, 3),
        ))
        self.assertEqual([request[0] for request in third_client.requests], ["GET", "POST"])
        self.assertEqual(len(third_manager.replacements), 1)

    async def test_refresh_response_validation(self):
        credential = self.oauth_credential()
        cases = [
            ([FakeResponse({}, 401)], "unauthorized"),
            ([FakeResponse({}, 500)], "temporary"),
            ([FakeResponse(ValueError("json"))], "invalid_response"),
            ([FakeResponse([], 200)], "invalid_response"),
            ([FakeResponse({"code": 1}, 200)], "invalid_response"),
            ([FakeResponse({"code": 0, "data": {}}, 200)], "invalid_response"),
            ([self.token_response(), FakeResponse(ValueError("json"))], "invalid_accounts"),
            ([self.token_response(), FakeResponse([], 200)], "invalid_accounts"),
            ([self.token_response(), FakeResponse({"code": 1}, 200)], "invalid_accounts"),
            ([self.token_response(), FakeResponse({"code": 0, "data": {}}, 200)], "invalid_accounts"),
            ([self.token_response(), FakeResponse({"code": 0, "data": {"accounts": []}}, 200)], "account_missing"),
        ]
        refresher = CredentialRefreshManager(now_factory=lambda: 1_000_000)
        for responses, expected in cases:
            with self.subTest(expected=expected, responses=responses):
                with self.assertRaisesRegex(CredentialRefreshError, expected):
                    await refresher._refresh_once(
                        FakeClient(responses),
                        "admin",
                        FakeManager(),
                        "cred",
                        _RefreshState(credential=credential, generation=0),
                    )

    async def test_refresh_selects_default_account_preserves_compatibility_and_generation(self):
        first = self.raw_account(lastLogin=False)
        credential = self.oauth_credential(
            account_id=None,
            enterprise_id="old-enterprise",
            upstream_responses={"login_token": {"raw": True}},
            compatibility_data={"legacy_full_response": {"old": True}},
        )
        manager = FakeManager(snapshot=(credential, 4), replace_results=(True, False))
        client = FakeClient([
            self.token_response(domain=None, refreshToken=None),
            FakeResponse({"code": 0, "data": {"accounts": ["bad", first]}}),
        ])
        refresher = CredentialRefreshManager(now_factory=lambda: 1_000_000)

        self.assertFalse(await refresher._refresh_once(
            client,
            "admin",
            manager,
            "cred",
            _RefreshState(credential=credential, generation=4),
        ))
        _, updated, generation, quota_changed = manager.replacements[1]
        self.assertEqual(generation, 5)
        self.assertFalse(quota_changed)
        self.assertEqual(manager.replacements[0][1]["refresh_token"], "refresh")
        self.assertEqual(updated["domain"], "copilot.tencent.com")
        self.assertEqual(updated["compatibility_data"], credential["compatibility_data"])
        self.assertEqual(updated["upstream_responses"]["login_token"], {"raw": True})
        self.assertIn("refresh", updated["upstream_responses"])

    async def test_switch_account_success_for_enterprise_and_personal(self):
        from src.codebuddy_oauth import TokenParser

        for account_type in ("ultimate", "personal"):
            with self.subTest(account_type=account_type):
                raw = self.raw_account(type=account_type)
                if account_type == "personal":
                    raw.pop("enterpriseId")
                normalized = TokenParser._normalize_account(raw)
                credential = self.oauth_credential(
                    account_id="old",
                    enterprise_id="old-enterprise",
                    accounts=[normalized],
                    compatibility_data={"legacy": True},
                    upstream_responses={"login_token": {"raw": True}},
                )
                manager = FakeManager(snapshot=(credential, 2))
                client = FakeClient([
                    self.token_response(domain=None, enterpriseId=None),
                    FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
                ])
                refresher = CredentialRefreshManager(
                    http_client_factory=lambda client=client: client,
                    now_factory=lambda: 1_000_000,
                )

                self.assertTrue(await refresher.switch_account(
                    "admin", manager, "cred", normalized["account_id"],
                ))
                request_url = client.requests[0][1]
                if account_type == "personal":
                    self.assertTrue(request_url.endswith("/v2/plugin/login/enterprise"))
                else:
                    self.assertTrue(request_url.endswith("/v2/plugin/login/enterprise/enterprise-1"))
                    self.assertEqual(client.requests[0][2]["headers"]["X-Enterprise-Id"], "enterprise-1")
                updated = manager.replacements[0][1]
                self.assertTrue(manager.replacements[0][3])
                self.assertEqual(updated["account_id"], normalized["account_id"])
                if account_type == "personal":
                    self.assertNotIn("enterprise_id", updated)
                    self.assertNotIn("enterprise_id", updated["user_info"])
                else:
                    self.assertEqual(updated["enterprise_id"], "enterprise-1")
                self.assertEqual(updated["compatibility_data"], {"legacy": True})
                self.assertIn("account_switch", updated["upstream_responses"])

    async def test_switching_to_current_account_preserves_quota_generation(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account()
        normalized = TokenParser._normalize_account(raw)
        credential = self.oauth_credential(
            account_id=normalized["account_id"],
            accounts=[normalized],
        )
        manager = FakeManager(snapshot=(credential, 2))
        client = FakeClient([
            self.token_response(),
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        refresher = CredentialRefreshManager(http_client_factory=lambda: client)

        self.assertTrue(await refresher.switch_account(
            "admin", manager, "cred", normalized["account_id"],
        ))

        self.assertFalse(manager.replacements[0][3])

    async def test_switch_waits_for_refresh_and_validates_local_context(self):
        refresher = CredentialRefreshManager()
        waiting = asyncio.create_task(asyncio.sleep(0))
        refresher._inflight["admin:cred"] = waiting
        manager = FakeManager(snapshot=None)
        with self.assertRaisesRegex(CredentialRefreshError, "credential_not_found"):
            await refresher.switch_account("admin", manager, "cred", "account")

        invalid_cases = [
            (self.oauth_credential(refresh_token=None, accounts=[]), "switch_unavailable"),
            (self.oauth_credential(accounts=[]), "account_not_found"),
            (self.oauth_credential(accounts=[{"account_id": "a", "type": "ultimate"}]), "account_invalid"),
        ]
        for credential, expected in invalid_cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(CredentialRefreshError, expected):
                    await refresher._perform_switch(
                        "admin", FakeManager(), "cred", "a", (credential, 0),
                    )

    async def test_switch_response_validation(self):
        from src.codebuddy_oauth import TokenParser

        raw = self.raw_account()
        normalized = TokenParser._normalize_account(raw)
        credential = self.oauth_credential(accounts=[normalized])
        cases = [
            ([FakeResponse(ValueError("json"))], "invalid_response"),
            ([FakeResponse({"code": 10081})], "ip_restricted"),
            ([FakeResponse({}, 500)], "temporary"),
            ([FakeResponse([], 200)], "invalid_response"),
            ([FakeResponse({"code": 1}, 200)], "invalid_response"),
            ([FakeResponse({"code": 0, "data": {}}, 200)], "invalid_response"),
            ([self.token_response(), FakeResponse(ValueError("json"))], "invalid_accounts"),
            ([self.token_response(), FakeResponse([], 200)], "invalid_accounts"),
            ([self.token_response(), FakeResponse({"code": 1}, 200)], "invalid_accounts"),
            ([self.token_response(), FakeResponse({"code": 0, "data": {}}, 200)], "invalid_accounts"),
            ([self.token_response(), FakeResponse({"code": 0, "data": {"accounts": []}})], "account_missing"),
        ]
        refresher = CredentialRefreshManager()
        for responses, expected in cases:
            with self.subTest(expected=expected, responses=responses):
                refresher._http_client_factory = lambda responses=responses: FakeClient(responses)
                with self.assertRaisesRegex(CredentialRefreshError, expected):
                    await refresher._perform_switch(
                        "admin", FakeManager(), "cred", normalized["account_id"], (credential, 0),
                    )

        manager = FakeManager(replace_result=False)
        refresher._http_client_factory = lambda: FakeClient([
            self.token_response(),
            FakeResponse({"code": 0, "data": {"accounts": [raw]}}),
        ])
        with self.assertRaisesRegex(CredentialRefreshError, "generation_conflict"):
            await refresher._perform_switch(
                "admin", manager, "cred", normalized["account_id"], (credential, 0),
            )


if __name__ == "__main__":
    unittest.main()
