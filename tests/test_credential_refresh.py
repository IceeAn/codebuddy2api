import tempfile
import unittest
from unittest import mock

import asyncio
import httpx

import config
from src.codebuddy_token_manager import CodeBuddyTokenManager, CodeBuddyTokenManagerRegistry
from src.credential_refresh import CredentialRefreshError, CredentialRefreshManager
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
    def __init__(self, snapshot=None, replace_result=True, infos=()):
        self.snapshot = snapshot
        self.replace_result = replace_result
        self.infos = list(infos)
        self.replacements = []

    def snapshot_credential_by_id(self, _credential_id):
        return self.snapshot

    def replace_credential_by_id(self, credential_id, data, *, expected_generation):
        self.replacements.append((credential_id, data, expected_generation))
        return self.replace_result

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
                        FakeClient(responses), "admin", FakeManager(), "cred", credential, 0,
                    )

    async def test_refresh_selects_default_account_preserves_compatibility_and_generation(self):
        first = self.raw_account(lastLogin=False)
        credential = self.oauth_credential(
            account_id=None,
            enterprise_id="old-enterprise",
            upstream_responses={"login_token": {"raw": True}},
            compatibility_data={"legacy_full_response": {"old": True}},
        )
        manager = FakeManager(snapshot=(credential, 4), replace_result=False)
        client = FakeClient([
            self.token_response(domain=None),
            FakeResponse({"code": 0, "data": {"accounts": ["bad", first]}}),
        ])
        refresher = CredentialRefreshManager(now_factory=lambda: 1_000_000)

        self.assertFalse(await refresher._refresh_once(
            client, "admin", manager, "cred", credential, 4,
        ))
        _, updated, generation = manager.replacements[0]
        self.assertEqual(generation, 4)
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
                self.assertEqual(updated["account_id"], normalized["account_id"])
                self.assertEqual(updated["compatibility_data"], {"legacy": True})
                self.assertIn("account_switch", updated["upstream_responses"])

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
