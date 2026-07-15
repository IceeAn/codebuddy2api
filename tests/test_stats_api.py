import threading
import unittest
from unittest import mock

import httpx

from src.api_key_store import api_key_store
from src.auth_types import SESSION_COOKIE_NAME
from src.session_store import session_store
from src.usage_stats_middleware import dropped_completion_events
from src.usage_stats_store import UsageStatsStore
from tests.helpers import TempConfigMixin, configure_users_file
from web import app


class StatsApiTests(TempConfigMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        configure_users_file(self.temp_path)
        self.session_id = session_store.create("admin")
        self.api_key = api_key_store.create_key("admin", "stats-test")["api_key"]
        dropped_completion_events.reset_for_tests()

    def tearDown(self):
        dropped_completion_events.reset_for_tests()
        super().tearDown()

    async def _get(self, path, *, session=False, api_key=False):
        headers = {}
        if session:
            headers["Cookie"] = f"{SESSION_COOKIE_NAME}={self.session_id}"
        if api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
        ) as client:
            return await client.get(path, headers=headers)

    async def test_overview_requires_session_and_is_private(self):
        unauthenticated = await self._get("/api/admin/stats/overview")
        api_key = await self._get("/api/admin/stats/overview", api_key=True)

        for response in (unauthenticated, api_key):
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_overview_converts_query_filters_and_combines_dropped_events(self):
        expected = {
            "totals": {"request_count": 2},
            "series": [],
            "dimensions": {},
            "breakdowns": {},
            "data_quality": {"dropped_events": 5},
        }
        dropped_completion_events.record("admin")
        dropped_completion_events.record("admin")
        dropped_completion_events.record("other")

        with mock.patch(
            "src.stats_router.usage_stats_store.get_overview",
            return_value=expected,
        ) as get, mock.patch.object(
            UsageStatsStore,
            "get_dropped_events",
            return_value=3,
        ) as get_dropped:
            response = await self._get(
                "/api/admin/stats/overview"
                "?start_at=100&end_at=200&timezone=Asia%2FTaipei"
                "&traffic=external&model=glm-5.2&api_key_id=key-1"
                "&credential_id=credential-1&outcome=success&granularity=day",
                session=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "admin")
        filters = args[1]
        self.assertEqual(filters.start_time, 100)
        self.assertEqual(filters.end_time, 200)
        self.assertEqual(filters.timezone, "Asia/Taipei")
        self.assertEqual(filters.traffic, "external")
        self.assertEqual(filters.model, "glm-5.2")
        self.assertEqual(filters.api_key_id, "key-1")
        self.assertEqual(filters.credential_id, "credential-1")
        self.assertEqual(filters.outcome, "success")
        self.assertEqual(filters.granularity, "day")
        get_dropped.assert_called_once_with("admin")
        self.assertEqual(kwargs, {"dropped_events": 5})

    async def test_overview_uses_documented_defaults(self):
        with mock.patch(
            "src.stats_router.usage_stats_store.get_overview",
            return_value={"data_quality": {}},
        ) as get:
            response = await self._get("/api/admin/stats/overview", session=True)

        self.assertEqual(response.status_code, 200)
        filters = get.call_args.args[1]
        self.assertIsNone(filters.start_time)
        self.assertIsNone(filters.end_time)
        self.assertEqual(filters.timezone, "UTC")
        self.assertEqual(filters.traffic, "all")
        self.assertEqual(filters.granularity, "auto")

    async def test_stats_store_reads_run_outside_the_event_loop_thread(self):
        event_loop_thread = threading.get_ident()
        store_thread = None

        def read_overview(*_args, **_kwargs):
            nonlocal store_thread
            store_thread = threading.get_ident()
            return {"data_quality": {}}

        with mock.patch(
            "src.stats_router.usage_stats_store.get_overview",
            side_effect=read_overview,
        ):
            response = await self._get("/api/admin/stats/overview", session=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(store_thread, event_loop_thread)

    async def test_request_list_converts_filters_and_page_snapshot(self):
        expected = {
            "items": [{"id": 4}],
            "page": 3,
            "page_size": 20,
            "total": 41,
            "total_pages": 3,
            "snapshot_id": 80,
            "snapshot_time": 7,
        }
        with mock.patch("src.stats_router.usage_stats_store.list_events", return_value=expected) as get:
            response = await self._get(
                "/api/admin/stats/requests"
                "?start_at=1&end_at=9&timezone=UTC&traffic=admin"
                "&model=glm-5.1&api_key_id=key-2&credential_id=credential-2"
                "&outcome=failure&page=3&page_size=20&snapshot_id=80&snapshot_time=7",
                session=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "admin")
        filters = args[1]
        self.assertEqual(filters.start_time, 1)
        self.assertEqual(filters.end_time, 9)
        self.assertEqual(filters.traffic, "admin")
        self.assertEqual(filters.model, "glm-5.1")
        self.assertEqual(filters.api_key_id, "key-2")
        self.assertEqual(filters.credential_id, "credential-2")
        self.assertEqual(filters.outcome, "failure")
        self.assertEqual(
            kwargs,
            {"page": 3, "page_size": 20, "snapshot_id": 80, "snapshot_time": 7},
        )

    async def test_request_list_uses_page_defaults(self):
        with mock.patch(
            "src.stats_router.usage_stats_store.list_events",
            return_value={"items": [], "page": 1, "page_size": 20, "total": 0},
        ) as get:
            response = await self._get("/api/admin/stats/requests", session=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            get.call_args.kwargs,
            {
                "page": 1,
                "page_size": 20,
                "snapshot_id": None,
                "snapshot_time": None,
            },
        )

    async def test_dimension_list_converts_filters_search_and_cursor(self):
        expected = {"items": [{"id": "glm", "label": "glm"}], "next_cursor": "next"}
        with mock.patch(
            "src.stats_router.usage_stats_store.list_dimension_values",
            return_value=expected,
        ) as get:
            response = await self._get(
                "/api/admin/stats/dimensions/models"
                "?start_at=1&end_at=9&timezone=UTC&traffic=external"
                "&api_key_id=key-1&search=glm&cursor=cursor&limit=25",
                session=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        args, kwargs = get.call_args
        self.assertEqual(args[:2], ("admin", "models"))
        self.assertEqual(args[2].start_time, 1)
        self.assertEqual(args[2].api_key_id, "key-1")
        self.assertEqual(
            kwargs,
            {"search": "glm", "cursor": "cursor", "limit": 25},
        )

    async def test_request_detail_is_user_scoped_and_missing_is_404(self):
        detail = {"id": 12, "source": "external_api"}
        with mock.patch(
            "src.stats_router.usage_stats_store.get_event",
            side_effect=[detail, None],
        ) as get:
            found = await self._get(
                "/api/admin/stats/requests/12?snapshot_id=20&snapshot_time=7",
                session=True,
            )
            missing = await self._get("/api/admin/stats/requests/13", session=True)

        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json(), detail)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), {"detail": "Usage event not found"})
        self.assertEqual(missing.headers["Cache-Control"], "private, no-store")
        self.assertEqual(
            get.call_args_list,
            [
                mock.call("admin", 12, snapshot_id=20, snapshot_time=7),
                mock.call("admin", 13, snapshot_id=None, snapshot_time=None),
            ],
        )

    async def test_store_value_errors_are_validation_responses(self):
        cases = (
            ("get_overview", "/api/admin/stats/overview"),
            ("list_events", "/api/admin/stats/requests"),
            ("list_dimension_values", "/api/admin/stats/dimensions/models"),
            ("get_event", "/api/admin/stats/requests/1"),
        )
        for method, path in cases:
            with self.subTest(method=method), mock.patch(
                f"src.stats_router.usage_stats_store.{method}",
                side_effect=ValueError("invalid stats query"),
            ):
                response = await self._get(path, session=True)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {"detail": "invalid stats query"})

    async def test_query_contract_rejects_invalid_enum_and_pagination_values(self):
        paths = (
            "/api/admin/stats/overview?traffic=invalid",
            "/api/admin/stats/overview?granularity=minute",
            "/api/admin/stats/requests?page=0",
            "/api/admin/stats/requests?page_size=0",
            "/api/admin/stats/requests?page_size=101",
            "/api/admin/stats/requests?snapshot_id=-1",
            "/api/admin/stats/requests/0",
            "/api/admin/stats/overview?start_at=253370764800",
            "/api/admin/stats/requests?end_at=253370764800",
            "/api/admin/stats/requests?snapshot_id=9223372036854775808",
            "/api/admin/stats/requests/1?snapshot_id=-1&snapshot_time=1",
            "/api/admin/stats/requests/1?snapshot_id=1&snapshot_time=253370764800",
            "/api/admin/stats/requests/9223372036854775808",
            "/api/admin/stats/dimensions/outcomes",
            "/api/admin/stats/dimensions/models?limit=0",
        )
        for path in paths:
            with self.subTest(path=path):
                response = await self._get(path, session=True)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    async def test_openapi_exposes_stats_as_session_protected_routes(self):
        response = await self._get("/openapi.json", session=True)
        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        for path, operation in (
            ("/api/admin/stats/overview", "get"),
            ("/api/admin/stats/requests", "get"),
            ("/api/admin/stats/requests/{id}", "get"),
            ("/api/admin/stats/dimensions/{dimension}", "get"),
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    paths[path][operation]["security"],
                    [{"SessionCookie": []}],
                )


if __name__ == "__main__":
    unittest.main()
