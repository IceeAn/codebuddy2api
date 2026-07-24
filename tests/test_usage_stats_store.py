import asyncio
import dataclasses
import base64
import json
import math
import sqlite3
import tempfile
import time
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from src.sqlite_database import DATABASE_FILENAME, SQLiteDatabase
from src.usage_stats_store import (
    CLEANUP_BATCH_SIZE,
    DETAIL_RETENTION_DAYS,
    LATENCY_BUCKET_UPPER_BOUNDS_MS,
    MAX_SERIES_POINTS,
    MAX_STATS_TIMESTAMP,
    SQLITE_MAX_INTEGER,
    PercentileEstimate,
    StatsFilters,
    TokenUsage,
    UsageEvent,
    UsageStatsStore,
    UsageStatsRetentionManager,
    approximate_percentile,
    latency_bucket_index,
    normalize_usage,
)


class UsageNormalizationTests(unittest.TestCase):
    def test_normalize_usage_supports_real_codebuddy_and_openai_fields(self):
        usage = normalize_usage({
            "prompt_tokens": 33,
            "completion_tokens": 131.0,
            "total_tokens": 164,
            "completion_tokens_details": {"reasoning_tokens": 128},
            "prompt_cache_hit_tokens": 7,
            "prompt_cache_miss_tokens": 26,
            "prompt_cache_write_tokens": 3,
            "credit": 0.12,
        })

        self.assertEqual(
            usage,
            TokenUsage(
                input_tokens=33,
                output_tokens=131,
                total_tokens=164,
                reasoning_tokens=128,
                cache_hit_tokens=7,
                cache_miss_tokens=26,
                cache_write_tokens=3,
                credit=0.12,
            ),
        )

        aliases = normalize_usage({
            "input_tokens": 10,
            "output_tokens": 4,
            "reasoning_tokens": 2,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 6,
        })
        self.assertEqual(aliases.input_tokens, 10)
        self.assertEqual(aliases.output_tokens, 4)
        self.assertIsNone(aliases.total_tokens)
        self.assertEqual(aliases.reasoning_tokens, 2)
        self.assertEqual(aliases.cache_hit_tokens, 5)
        self.assertIsNone(aliases.cache_miss_tokens)
        self.assertEqual(aliases.cache_write_tokens, 6)

    def test_normalize_usage_keeps_missing_and_invalid_values_none(self):
        self.assertEqual(normalize_usage(None), TokenUsage())
        self.assertEqual(normalize_usage([]), TokenUsage())
        usage = normalize_usage({
            "prompt_tokens": True,
            "completion_tokens": -1,
            "total_tokens": 1.5,
            "completion_tokens_details": "invalid",
            "prompt_tokens_details": {"cached_tokens": 8},
            "completion_thinking_tokens": 3,
            "cached_tokens": 9,
            "credit": math.inf,
        })
        self.assertIsNone(usage.input_tokens)
        self.assertIsNone(usage.output_tokens)
        self.assertIsNone(usage.total_tokens)
        self.assertEqual(usage.reasoning_tokens, 3)
        self.assertEqual(usage.cache_hit_tokens, 8)
        self.assertIsNone(usage.cache_miss_tokens)
        self.assertIsNone(usage.cache_write_tokens)
        self.assertIsNone(usage.credit)

    def test_fixed_latency_histogram_returns_nearest_rank_upper_bound(self):
        self.assertEqual(latency_bucket_index(0), 0)
        self.assertEqual(latency_bucket_index(50), 1)
        self.assertEqual(latency_bucket_index(100), 2)
        self.assertEqual(
            latency_bucket_index(LATENCY_BUCKET_UPPER_BOUNDS_MS[-1]),
            len(LATENCY_BUCKET_UPPER_BOUNDS_MS),
        )
        self.assertEqual(latency_bucket_index(9999999), len(LATENCY_BUCKET_UPPER_BOUNDS_MS))
        self.assertIsNone(latency_bucket_index(None))
        self.assertIsNone(latency_bucket_index(-1))
        self.assertIsNone(latency_bucket_index(float("nan")))

        counts = {0: 1, 3: 8, len(LATENCY_BUCKET_UPPER_BOUNDS_MS) - 1: 1}
        self.assertEqual(
            approximate_percentile(counts, 0.5),
            PercentileEstimate(LATENCY_BUCKET_UPPER_BOUNDS_MS[3], False),
        )
        self.assertEqual(
            approximate_percentile(counts, 0.95),
            PercentileEstimate(LATENCY_BUCKET_UPPER_BOUNDS_MS[-1], False),
        )
        self.assertEqual(approximate_percentile({}, 0.95), PercentileEstimate(None, False))
        self.assertEqual(
            approximate_percentile({len(LATENCY_BUCKET_UPPER_BOUNDS_MS): 1}, 0.95),
            PercentileEstimate(LATENCY_BUCKET_UPPER_BOUNDS_MS[-1], True),
        )
        with self.assertRaisesRegex(ValueError, "percentile"):
            approximate_percentile(counts, 0)


class UsageStatsStoreTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temp_dir.name) / DATABASE_FILENAME
        self.store = UsageStatsStore(self.database_path)
        self.now = int(time.time()) // 3600 * 3600
        with SQLiteDatabase(self.database_path).connect():
            pass

    def tearDown(self):
        self._temp_dir.cleanup()

    def event(self, **overrides):
        values = {
            "source": "external_api",
            "requested_model": "provider/glm-5.2",
            "upstream_model": "glm-5.2",
            "occurred_at": self.now,
            "api_key_id": "key-1",
            "api_key_name": "Production",
            "credential_id": "credential-1",
            "credential_label": "Alice",
            "outcome": "success",
            "http_status": 200,
            "result_status": 200,
            "client_stream": True,
            "thinking_mode": "enabled",
            "message_count": 2,
            "tool_count": 1,
            "request_bytes": 100,
            "response_bytes": 200,
            "retry_count": 1,
            "tool_call_count": 2,
            "finish_reason": "stop",
            "input_tokens": 10,
            "output_tokens": 6,
            "total_tokens": 16,
            "reasoning_tokens": 4,
            "cache_hit_tokens": 3,
            "cache_miss_tokens": 7,
            "cache_write_tokens": 1,
            "credit": 0.25,
            "duration_ms": 900,
            "first_event_ms": 100,
            "first_output_ms": 300,
            "first_reasoning_ms": 200,
            "first_content_ms": 400,
        }
        values.update(overrides)
        return UsageEvent(**values)

    def record(self, event, username="alice"):
        return self.store.record_event(event, username=username)

    def test_record_event_persists_only_desensitized_columns_and_hourly_data(self):
        with mock.patch(
            "src.usage_stats_store.asdict",
            wraps=dataclasses.asdict,
        ) as convert:
            event_id = self.record(self.event())
        self.assertIsInstance(event_id, int)
        convert.assert_called_once()

        with SQLiteDatabase(self.database_path).connect() as connection:
            detail = dict(connection.execute(
                "SELECT * FROM usage_events WHERE id = ?", (event_id,)
            ).fetchone())
            hourly = dict(connection.execute("SELECT * FROM usage_hourly").fetchone())
            histograms = connection.execute(
                "SELECT metric, bucket_index, sample_count "
                "FROM usage_latency_histogram ORDER BY metric"
            ).fetchall()
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(usage_events)")
            }

        self.assertTrue(columns.isdisjoint({
            "prompt",
            "response",
            "headers",
            "bearer_token",
            "session_id",
            "tool_arguments",
        }))
        self.assertEqual(detail["requested_model"], "provider/glm-5.2")
        self.assertEqual(detail["upstream_model"], "glm-5.2")
        self.assertEqual(detail["client_stream"], 1)
        self.assertEqual(hourly["model"], "glm-5.2")
        self.assertEqual(hourly["request_count"], 1)
        self.assertEqual(hourly["total_tokens_sum"], 16)
        self.assertEqual(hourly["usage_known_count"], 1)
        self.assertEqual(hourly["credit_sum"], 0.25)
        self.assertEqual({row[0] for row in histograms}, {"first_output", "total"})
        self.assertEqual({row[2] for row in histograms}, {1})

    def test_record_event_merges_hourly_dimensions_and_preserves_null_usage(self):
        first_id = self.record(self.event())
        second_id = self.record(self.event(
            occurred_at=self.now + 10,
            total_tokens=None,
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
            cache_write_tokens=None,
            credit=None,
            duration_ms=None,
            first_output_ms=None,
        ))

        self.assertGreater(second_id, first_id)
        with SQLiteDatabase(self.database_path).connect() as connection:
            hourly = dict(connection.execute("SELECT * FROM usage_hourly").fetchone())

        self.assertEqual(hourly["request_count"], 2)
        self.assertEqual(hourly["success_count"], 2)
        self.assertEqual(hourly["usage_known_count"], 1)
        self.assertEqual(hourly["total_tokens_known_count"], 1)
        self.assertEqual(hourly["credit_known_count"], 1)

    def test_cancelled_event_is_persisted_without_success_latency_samples(self):
        event_id = self.record(self.event(
            outcome="cancelled",
            http_status=None,
            result_status=None,
            error_type="client_disconnect",
        ))

        detail = self.store.get_event("alice", event_id)
        overview = self.store.get_overview("alice", StatsFilters(outcome="cancelled"))
        with SQLiteDatabase(self.database_path).connect() as connection:
            hourly = dict(connection.execute("SELECT * FROM usage_hourly").fetchone())
            histogram_count = connection.execute(
                "SELECT COUNT(*) FROM usage_latency_histogram"
            ).fetchone()[0]

        self.assertEqual(detail["outcome"], "cancelled")
        self.assertEqual(hourly["cancelled_count"], 1)
        self.assertEqual(overview["totals"]["request_count"], 1)
        self.assertIsNone(overview["totals"]["p95_total_ms"])
        self.assertEqual(histogram_count, 0)

    def test_overview_returns_totals_series_dimensions_breakdowns_and_quality(self):
        self.record(self.event())
        self.record(self.event(
            occurred_at=self.now + 3600,
            source="admin_playground",
            api_key_id=None,
            api_key_name=None,
            outcome="failure",
            http_status=429,
            result_status=429,
            error_type="rate_limit",
            total_tokens=None,
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            cache_hit_tokens=None,
            cache_miss_tokens=None,
            cache_write_tokens=None,
            credit=None,
            duration_ms=1500,
            first_output_ms=None,
        ))
        self.record(self.event(
            occurred_at=self.now + 7200,
            source="credential_test",
            requested_model="glm-5.1",
            upstream_model=None,
            api_key_id=None,
            api_key_name=None,
            credential_id="credential-2",
            credential_label="Bob",
            total_tokens=4,
            credit=0.1,
            duration_ms=10000,
            first_output_ms=5000,
        ))

        filters = StatsFilters(
            start_time=self.now - 1,
            end_time=self.now + 10800,
            timezone="Asia/Taipei",
            granularity="hour",
        )
        overview = self.store.get_overview("alice", filters, dropped_events=3)

        self.assertEqual(set(overview), {
            "totals", "series", "dimensions", "breakdowns", "data_quality"
        })
        self.assertEqual(overview["totals"], {
            "request_count": 3,
            "success_rate": 2 / 3,
            "input_tokens": 20,
            "output_tokens": 12,
            "total_tokens": 20,
            "cache_hit_tokens": 6,
            "cache_miss_tokens": 14,
            "total_credit": 0.35,
            "p95_first_output_ms": 10000,
            "p95_first_output_ms_overflow": False,
            "p95_total_ms": 30000,
            "p95_total_ms_overflow": False,
            "usage_coverage": 2 / 3,
        })
        self.assertEqual(len(overview["series"]), 4)
        self.assertEqual(
            [point["request_count"] for point in overview["series"]],
            [0, 1, 1, 1],
        )
        self.assertEqual(overview["dimensions"]["models"], ["glm-5.2", "glm-5.1"])
        self.assertEqual(overview["dimensions"]["outcomes"], ["failure", "success"])
        self.assertEqual(
            overview["dimensions"]["api_keys"],
            [{"id": "key-1", "name": "Production"}],
        )
        self.assertEqual(
            overview["dimensions"]["credentials"],
            [
                {"id": "credential-1", "label": "Alice"},
                {"id": "credential-2", "label": "Bob"},
            ],
        )
        self.assertEqual(overview["breakdowns"]["models"][0]["model"], "glm-5.2")
        self.assertEqual(overview["breakdowns"]["models"][0]["request_count"], 2)
        self.assertEqual(overview["breakdowns"]["api_keys"][0]["id"], "key-1")
        self.assertEqual(overview["breakdowns"]["credentials"][0]["id"], "credential-1")
        self.assertEqual(overview["data_quality"], {
            "usage_coverage": 2 / 3,
            "dropped_events": 3,
            "detail_retention_days": DETAIL_RETENTION_DAYS,
            "boundary_precision": "exact",
        })

    def test_overview_uses_a_distinct_latency_overflow_bucket(self):
        self.record(self.event(duration_ms=600_000, first_output_ms=900_000))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now,
            end_time=self.now + 3600,
            granularity="hour",
        ))

        self.assertEqual(overview["totals"]["p95_total_ms"], 600_000)
        self.assertIs(overview["totals"]["p95_total_ms_overflow"], True)
        self.assertEqual(overview["totals"]["p95_first_output_ms"], 600_000)
        self.assertIs(overview["totals"]["p95_first_output_ms_overflow"], True)

    def test_partial_boundary_recalculation_uses_strict_latency_upper_bounds(self):
        self.record(self.event(
            occurred_at=self.now + 1,
            duration_ms=100,
            first_output_ms=50,
        ))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now + 1,
            end_time=self.now + 3599,
            granularity="hour",
        ))

        self.assertEqual(overview["totals"]["p95_total_ms"], 250)
        self.assertEqual(overview["totals"]["p95_first_output_ms"], 100)

    def test_overview_fills_empty_periods_without_fabricating_unknown_metrics(self):
        day_start = self.now // 86400 * 86400
        self.record(self.event(occurred_at=day_start, total_tokens=2, credit=0.1))
        self.record(self.event(
            occurred_at=day_start + 6 * 86400,
            total_tokens=3,
            credit=0.2,
        ))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=day_start,
            end_time=day_start + 7 * 86400,
            timezone="UTC",
            granularity="day",
        ))

        self.assertEqual(len(overview["series"]), 7)
        self.assertEqual(
            [point["request_count"] for point in overview["series"]],
            [1, 0, 0, 0, 0, 0, 1],
        )
        empty = overview["series"][1]
        self.assertEqual(empty["total_tokens"], 0)
        self.assertEqual(empty["total_credit"], 0)
        self.assertIsNone(empty["success_rate"])
        self.assertIsNone(empty["p95_total_ms"])

    def test_overview_returns_zero_for_unknown_credit_aggregates(self):
        self.record(self.event(credit=None))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now,
            end_time=self.now + 3600,
            timezone="UTC",
            granularity="hour",
        ))

        self.assertEqual(overview["totals"]["total_credit"], 0)
        self.assertEqual(overview["series"][0]["request_count"], 1)
        self.assertEqual(overview["series"][0]["total_credit"], 0)
        self.assertEqual(overview["breakdowns"]["models"][0]["total_credit"], 0)
        self.assertEqual(overview["breakdowns"]["api_keys"][0]["total_credit"], 0)
        self.assertEqual(
            overview["breakdowns"]["credentials"][0]["total_credit"],
            0,
        )

    def test_overview_returns_all_default_dimensions_and_limits_rankings(self):
        for index in range(105):
            self.record(self.event(
                occurred_at=self.now + index,
                requested_model=f"model-{index:03d}",
                upstream_model=None,
                api_key_id=f"key-{index:03d}",
                api_key_name=f"Key {index:03d}",
                credential_id=f"credential-{index:03d}",
                credential_label=f"Credential {index:03d}",
            ))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now,
            end_time=self.now + 3600,
        ))

        self.assertEqual(len(overview["dimensions"]["models"]), 105)
        self.assertEqual(len(overview["dimensions"]["api_keys"]), 105)
        self.assertEqual(len(overview["dimensions"]["credentials"]), 105)
        self.assertEqual(len(overview["breakdowns"]["models"]), 20)
        self.assertEqual(len(overview["breakdowns"]["api_keys"]), 20)
        self.assertEqual(len(overview["breakdowns"]["credentials"]), 20)

    def test_dimension_pages_use_searchable_snapshot_bound_keyset_cursors(self):
        for index in range(25):
            self.record(self.event(
                occurred_at=self.now + index,
                requested_model=f"model-{index:02d}",
                upstream_model=None,
            ))
        self.record(self.event(
            occurred_at=self.now + 100,
            requested_model="model-24",
            upstream_model=None,
        ))
        filters = StatsFilters(start_time=self.now, end_time=self.now + 3600)

        first = self.store.list_dimension_values(
            "alice", "models", filters, limit=10
        )
        second = self.store.list_dimension_values(
            "alice",
            "models",
            filters,
            cursor=first["next_cursor"],
            limit=10,
        )
        searched = self.store.list_dimension_values(
            "alice", "models", filters, search="model-2", limit=10
        )

        self.assertEqual(first["items"][0]["id"], "model-24")
        self.assertEqual(first["items"][0]["label"], "model-24")
        self.assertEqual(first["items"][0]["request_count"], 2)
        self.assertEqual(len(first["items"]), 10)
        self.assertIsInstance(first["next_cursor"], str)
        self.assertTrue(
            {item["id"] for item in first["items"]}.isdisjoint(
                {item["id"] for item in second["items"]}
            )
        )
        self.assertEqual(
            [item["id"] for item in searched["items"]],
            ["model-24", "model-20", "model-21", "model-22", "model-23"],
        )
        api_keys = self.store.list_dimension_values(
            "alice", "api_keys", filters, search="Production", limit=10
        )
        self.assertEqual(api_keys["items"][0]["id"], "key-1")
        self.assertEqual(api_keys["items"][0]["label"], "Production")
        empty = self.store.list_dimension_values(
            "alice",
            "credentials",
            StatsFilters(
                start_time=self.now,
                end_time=self.now + 3600,
                model="missing-model",
            ),
        )
        self.assertEqual(empty, {"items": [], "next_cursor": None})
        with self.assertRaisesRegex(ValueError, "cursor"):
            self.store.list_dimension_values(
                "alice",
                "models",
                StatsFilters(start_time=self.now + 1, end_time=self.now + 3600),
                cursor=first["next_cursor"],
            )

    def test_dimension_search_matches_identifier_before_aggregating_all_labels(self):
        self.record(self.event(
            occurred_at=self.now + 10,
            api_key_id="shared-key",
            api_key_name="Production",
            total_tokens=10,
        ))
        self.record(self.event(
            occurred_at=self.now + 20,
            api_key_id="shared-key",
            api_key_name="Renamed",
            total_tokens=20,
        ))

        page = self.store.list_dimension_values(
            "alice",
            "api_keys",
            StatsFilters(start_time=self.now, end_time=self.now + 3600),
            search="Production",
        )

        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["id"], "shared-key")
        self.assertEqual(page["items"][0]["label"], "Renamed")
        self.assertEqual(page["items"][0]["request_count"], 2)
        self.assertEqual(page["items"][0]["total_tokens"], 30)

    def test_dimension_cursor_keeps_ranking_at_the_first_page_event_snapshot(self):
        for model, count in (("model-a", 3), ("model-b", 2), ("model-c", 1)):
            for offset in range(count):
                self.record(self.event(
                    occurred_at=self.now + 100 + offset,
                    requested_model=model,
                    upstream_model=None,
                ))
        filters = StatsFilters(start_time=self.now)

        first = self.store.list_dimension_values(
            "alice", "models", filters, limit=1
        )
        for offset in range(5):
            self.record(self.event(
                occurred_at=self.now + 50 + offset,
                requested_model="model-c",
                upstream_model=None,
            ))
        second = self.store.list_dimension_values(
            "alice",
            "models",
            filters,
            cursor=first["next_cursor"],
            limit=1,
        )
        self.assertIsNotNone(second["next_cursor"])
        third = self.store.list_dimension_values(
            "alice",
            "models",
            filters,
            cursor=second["next_cursor"],
            limit=1,
        )

        self.assertEqual(
            [(page["items"][0]["id"], page["items"][0]["request_count"])
             for page in (first, second, third)],
            [("model-a", 3), ("model-b", 2), ("model-c", 1)],
        )
        self.assertIsNone(third["next_cursor"])

    def test_dimension_cursor_snapshot_applies_to_replaced_boundary_hours(self):
        for model, count in (("model-a", 2), ("model-b", 1)):
            for offset in range(count):
                self.record(self.event(
                    occurred_at=self.now + 100 + offset,
                    requested_model=model,
                    upstream_model=None,
                ))
        filters = StatsFilters(
            start_time=self.now + 25,
            end_time=self.now + 3590,
        )

        first = self.store.list_dimension_values(
            "alice", "models", filters, limit=1
        )
        for offset in range(5):
            self.record(self.event(
                occurred_at=self.now + 50 + offset,
                requested_model="model-b",
                upstream_model=None,
            ))
        second = self.store.list_dimension_values(
            "alice",
            "models",
            filters,
            cursor=first["next_cursor"],
            limit=1,
        )

        self.assertEqual(first["items"][0]["request_count"], 2)
        self.assertEqual(second["items"][0]["id"], "model-b")
        self.assertEqual(second["items"][0]["request_count"], 1)
        self.assertIsNone(second["next_cursor"])

    def test_dimension_cursor_snapshot_excludes_labels_written_after_first_page(self):
        for offset in range(2):
            self.record(self.event(
                occurred_at=self.now + 100 + offset,
                api_key_id="key-a",
                api_key_name="Key A",
            ))
        self.record(self.event(
            occurred_at=self.now + 200,
            api_key_id="key-b",
            api_key_name="Before",
        ))
        filters = StatsFilters(start_time=self.now)
        first = self.store.list_dimension_values(
            "alice", "api_keys", filters, limit=1
        )

        self.record(self.event(
            occurred_at=self.now + 50,
            api_key_id="key-b",
            api_key_name="After",
        ))
        second = self.store.list_dimension_values(
            "alice",
            "api_keys",
            filters,
            cursor=first["next_cursor"],
            limit=1,
        )

        self.assertEqual(second["items"][0]["id"], "key-b")
        self.assertEqual(second["items"][0]["label"], "Before")
        self.assertEqual(second["items"][0]["request_count"], 1)

    def test_dimension_cursor_rejects_snapshot_ahead_of_database_sequence(self):
        for model in ("model-a", "model-b"):
            self.record(self.event(requested_model=model, upstream_model=None))
        filters = StatsFilters(start_time=self.now, end_time=self.now + 3600)
        first = self.store.list_dimension_values(
            "alice", "models", filters, limit=1
        )
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "UPDATE sqlite_sequence SET seq = 0 WHERE name = 'usage_events'"
            )

        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.store.list_dimension_values(
                "alice",
                "models",
                filters,
                cursor=first["next_cursor"],
                limit=1,
            )

    def test_dimension_cursor_rejects_snapshot_crossed_by_detail_cleanup(self):
        for model in ("model-a", "model-b"):
            self.record(self.event(requested_model=model, upstream_model=None))
        filters = StatsFilters(start_time=self.now, end_time=self.now + 3600)
        first = self.store.list_dimension_values(
            "alice", "models", filters, limit=1
        )
        encoded = first["next_cursor"].encode("ascii")
        payload = json.loads(base64.urlsafe_b64decode(
            encoded + b"=" * (-len(encoded) % 4)
        ))
        snapshot_cutoff = (
            payload["snapshot_time"] - DETAIL_RETENTION_DAYS * 86400
        )
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "INSERT INTO usage_retention_state(id, detail_cutoff) VALUES (1, ?)",
                (snapshot_cutoff + 1,),
            )

        with self.assertRaisesRegex(ValueError, "fetch page 1 again"):
            self.store.list_dimension_values(
                "alice",
                "models",
                filters,
                cursor=first["next_cursor"],
                limit=1,
            )

    def test_dimension_cursor_accepts_cleanup_at_snapshot_cutoff(self):
        for model in ("model-a", "model-b"):
            self.record(self.event(requested_model=model, upstream_model=None))
        filters = StatsFilters(start_time=self.now, end_time=self.now + 3600)
        first = self.store.list_dimension_values(
            "alice", "models", filters, limit=1
        )
        encoded = first["next_cursor"].encode("ascii")
        payload = json.loads(base64.urlsafe_b64decode(
            encoded + b"=" * (-len(encoded) % 4)
        ))
        snapshot_cutoff = (
            payload["snapshot_time"] - DETAIL_RETENTION_DAYS * 86400
        )
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute(
                "INSERT INTO usage_retention_state(id, detail_cutoff) VALUES (1, ?)",
                (snapshot_cutoff,),
            )

        second = self.store.list_dimension_values(
            "alice",
            "models",
            filters,
            cursor=first["next_cursor"],
            limit=1,
        )

        self.assertEqual(len(second["items"]), 1)
        self.assertIsNone(second["next_cursor"])

    def test_dimension_cursor_rejects_count_outside_sqlite_integer_range(self):
        for model in ("model-a", "model-b"):
            self.record(self.event(requested_model=model, upstream_model=None))
        filters = StatsFilters(start_time=self.now, end_time=self.now + 3600)
        first = self.store.list_dimension_values(
            "alice", "models", filters, limit=1
        )
        encoded = first["next_cursor"].encode("ascii")
        payload = json.loads(base64.urlsafe_b64decode(
            encoded + b"=" * (-len(encoded) % 4)
        ))
        payload["count"] = SQLITE_MAX_INTEGER + 1
        tampered = base64.urlsafe_b64encode(json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")).rstrip(b"=").decode("ascii")

        with self.assertRaisesRegex(ValueError, "cursor"):
            self.store.list_dimension_values(
                "alice", "models", filters, cursor=tampered, limit=1
            )

    def test_dimension_page_validates_dimension_search_cursor_and_limit(self):
        cases = (
            lambda: self.store.list_dimension_values("alice", "outcomes"),
            lambda: self.store.list_dimension_values("alice", "models", search="x" * 101),
            lambda: self.store.list_dimension_values("alice", "models", cursor="invalid"),
            lambda: self.store.list_dimension_values("alice", "models", limit=0),
        )
        for operation in cases:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()

    def test_overview_uses_details_once_for_a_partial_range_inside_one_hour(self):
        self.record(self.event(occurred_at=self.now + 10, total_tokens=1, credit=0.01))
        self.record(self.event(
            occurred_at=self.now + 20,
            total_tokens=2,
            credit=0.02,
            duration_ms=None,
            first_output_ms=None,
        ))
        self.record(self.event(
            occurred_at=self.now + 30,
            total_tokens=3,
            credit=0.03,
            outcome="failure",
        ))
        self.record(self.event(occurred_at=self.now + 40, total_tokens=4, credit=0.04))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now + 15,
            end_time=self.now + 35,
            granularity="hour",
        ))

        self.assertEqual(overview["totals"]["request_count"], 2)
        self.assertEqual(overview["totals"]["total_tokens"], 5)
        self.assertEqual(overview["totals"]["input_tokens"], 20)
        self.assertEqual(overview["totals"]["output_tokens"], 12)
        self.assertEqual(overview["totals"]["cache_hit_tokens"], 6)
        self.assertEqual(overview["totals"]["cache_miss_tokens"], 14)
        self.assertAlmostEqual(overview["totals"]["total_credit"], 0.05)
        self.assertEqual(len(overview["series"]), 1)
        self.assertEqual(overview["data_quality"]["boundary_precision"], "exact")

    def test_overview_supports_one_sided_partial_ranges(self):
        self.record(self.event(occurred_at=self.now + 10, total_tokens=2))
        self.record(self.event(occurred_at=self.now + 20, total_tokens=3))

        from_start = self.store.get_overview("alice", StatsFilters(
            start_time=self.now + 15,
            granularity="hour",
        ))
        until_end = self.store.get_overview("alice", StatsFilters(
            end_time=self.now + 15,
            granularity="hour",
        ))

        self.assertEqual(from_start["totals"]["request_count"], 1)
        self.assertEqual(from_start["totals"]["total_tokens"], 3)
        self.assertEqual(until_end["totals"]["request_count"], 1)
        self.assertEqual(until_end["totals"]["total_tokens"], 2)
        self.assertEqual(from_start["data_quality"]["boundary_precision"], "exact")
        self.assertEqual(until_end["data_quality"]["boundary_precision"], "exact")

    def test_overview_merges_two_detail_boundaries_with_complete_hourly_rows(self):
        events = (
            self.event(occurred_at=self.now + 10, total_tokens=1, duration_ms=50),
            self.event(
                occurred_at=self.now + 200,
                total_tokens=2,
                credit=0.02,
                duration_ms=250,
                first_output_ms=100,
            ),
            self.event(
                occurred_at=self.now + 3600,
                total_tokens=None,
                reasoning_tokens=8,
                credit=None,
                duration_ms=2000,
                first_output_ms=1000,
            ),
            self.event(
                occurred_at=self.now + 7200 + 100,
                total_tokens=4,
                credit=0.04,
                duration_ms=10000,
                first_output_ms=5000,
            ),
            self.event(occurred_at=self.now + 7200 + 300, total_tokens=8, duration_ms=30000),
        )
        for event in events:
            self.record(event)

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now + 100,
            end_time=self.now + 7200 + 200,
            granularity="hour",
        ))

        self.assertEqual(overview["totals"]["request_count"], 3)
        self.assertEqual(overview["totals"]["total_tokens"], 6)
        self.assertAlmostEqual(overview["totals"]["total_credit"], 0.06)
        self.assertEqual(overview["totals"]["usage_coverage"], 2 / 3)
        self.assertEqual(overview["totals"]["p95_first_output_ms"], 10000)
        self.assertEqual(overview["totals"]["p95_total_ms"], 30000)
        self.assertEqual([point["request_count"] for point in overview["series"]], [1, 1, 1])
        self.assertEqual(overview["data_quality"]["boundary_precision"], "exact")

    def test_overview_keeps_aligned_hours_on_rollups_without_details(self):
        self.record(self.event(occurred_at=self.now, total_tokens=3))
        self.record(self.event(occurred_at=self.now + 3600, total_tokens=5))
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.execute("DELETE FROM usage_events")

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now,
            end_time=self.now + 7200,
            granularity="hour",
        ))

        self.assertEqual(overview["totals"]["request_count"], 2)
        self.assertEqual(overview["totals"]["total_tokens"], 8)
        self.assertEqual(overview["totals"]["input_tokens"], 20)
        self.assertEqual(overview["totals"]["output_tokens"], 12)
        self.assertEqual(overview["totals"]["cache_hit_tokens"], 6)
        self.assertEqual(overview["totals"]["cache_miss_tokens"], 14)
        self.assertEqual(overview["data_quality"]["boundary_precision"], "exact")

    def test_overview_applies_all_filters_and_null_coverage_to_boundary_details(self):
        matching = {
            "occurred_at": self.now + 100,
            "source": "external_api",
            "upstream_model": "glm-5.2",
            "api_key_id": "key-1",
            "credential_id": "credential-1",
            "outcome": "success",
        }
        self.record(self.event(
            **matching,
            total_tokens=None,
            reasoning_tokens=9,
            credit=None,
            duration_ms=900,
            first_output_ms=300,
        ))
        self.record(self.event(
            **{**matching, "occurred_at": self.now + 200},
            total_tokens=4,
            credit=0.1,
            duration_ms=5000,
            first_output_ms=2000,
        ))
        self.record(self.event(
            **{**matching, "occurred_at": self.now + 300, "source": "admin_playground"},
            total_tokens=100,
        ))
        self.record(self.event(
            **{**matching, "occurred_at": self.now + 400, "outcome": "failure"},
            total_tokens=100,
        ))
        self.record(self.event(
            **{**matching, "occurred_at": self.now + 150, "upstream_model": "glm-5.1"},
            total_tokens=100,
        ))
        self.record(self.event(
            **{
                **matching,
                "occurred_at": self.now + 160,
                "api_key_id": "key-2",
                "api_key_name": "Staging",
            },
            total_tokens=100,
        ))
        self.record(self.event(
            **{
                **matching,
                "occurred_at": self.now + 170,
                "credential_id": "credential-2",
                "credential_label": "Bob",
            },
            total_tokens=100,
        ))
        self.record(self.event(
            **{**matching, "occurred_at": self.now + 180, "outcome": "failure"},
            total_tokens=100,
        ))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=self.now + 50,
            end_time=self.now + 250,
            traffic="external",
            model="glm-5.2",
            api_key_id="key-1",
            credential_id="credential-1",
            outcome="success",
            granularity="hour",
        ))

        self.assertEqual(overview["totals"]["request_count"], 2)
        self.assertEqual(overview["totals"]["total_tokens"], 4)
        self.assertEqual(overview["totals"]["total_credit"], 0.1)
        self.assertEqual(overview["totals"]["usage_coverage"], 0.5)
        self.assertEqual(overview["totals"]["p95_first_output_ms"], 5000)
        self.assertEqual(overview["totals"]["p95_total_ms"], 10000)
        self.assertEqual(overview["dimensions"], {
            "models": ["glm-5.2", "glm-5.1"],
            "api_keys": [
                {"id": "key-1", "name": "Production"},
                {"id": "key-2", "name": "Staging"},
            ],
            "credentials": [
                {"id": "credential-1", "label": "Alice"},
                {"id": "credential-2", "label": "Bob"},
            ],
            "outcomes": ["failure", "success"],
        })
        self.assertEqual(
            [row["model"] for row in overview["breakdowns"]["models"]],
            ["glm-5.2"],
        )
        self.assertEqual(
            [row["id"] for row in overview["breakdowns"]["api_keys"]],
            ["key-1"],
        )
        self.assertEqual(
            [row["id"] for row in overview["breakdowns"]["credentials"]],
            ["credential-1"],
        )

        api_key_only = self.store.get_overview("alice", StatsFilters(
            start_time=self.now + 50,
            end_time=self.now + 250,
            api_key_id="key-1",
            granularity="hour",
        ))
        self.assertEqual(
            api_key_only["dimensions"]["api_keys"],
            [
                {"id": "key-1", "name": "Production"},
                {"id": "key-2", "name": "Staging"},
            ],
        )

    def test_overview_honors_non_utc_local_day_boundaries(self):
        zone = ZoneInfo("Asia/Kathmandu")
        local_midnight = datetime.fromtimestamp(self.now, zone).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        start = int(local_midnight.timestamp())
        end = start + 86400
        self.record(self.event(occurred_at=start - 1, total_tokens=100))
        self.record(self.event(occurred_at=start, total_tokens=2))
        self.record(self.event(occurred_at=end - 1, total_tokens=3))
        self.record(self.event(occurred_at=end, total_tokens=100))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=start,
            end_time=end,
            timezone="Asia/Kathmandu",
            granularity="day",
        ))

        self.assertEqual(overview["totals"]["request_count"], 2)
        self.assertEqual(overview["totals"]["total_tokens"], 5)
        self.assertEqual(len(overview["series"]), 1)
        self.assertEqual(overview["series"][0]["period"], local_midnight.date().isoformat())
        self.assertEqual(overview["data_quality"]["boundary_precision"], "exact")

    def test_overview_splits_internal_day_boundaries_in_fractional_timezone(self):
        zone = ZoneInfo("Asia/Kathmandu")
        local_today = datetime.fromtimestamp(self.now, zone).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        start = int(local_today.timestamp()) - 2 * 86400
        internal_midnight = start + 86400
        end = start + 2 * 86400
        self.record(self.event(occurred_at=internal_midnight - 1, total_tokens=2))
        self.record(self.event(occurred_at=internal_midnight, total_tokens=3))

        overview = self.store.get_overview("alice", StatsFilters(
            start_time=start,
            end_time=end,
            timezone="Asia/Kathmandu",
            granularity="day",
        ))

        self.assertEqual(
            [(point["period"], point["request_count"]) for point in overview["series"]],
            [
                (datetime.fromtimestamp(start, zone).date().isoformat(), 1),
                (datetime.fromtimestamp(internal_midnight, zone).date().isoformat(), 1),
            ],
        )
        self.assertEqual(overview["data_quality"]["boundary_precision"], "exact")

    def test_overview_marks_expired_partial_boundary_as_hourly_approximate(self):
        query_now = self.now + 1800
        old_hour = (
            query_now - DETAIL_RETENTION_DAYS * 86400 - 7200
        ) // 3600 * 3600
        with mock.patch("src.usage_stats_store.time.time", return_value=query_now):
            self.record(self.event(occurred_at=old_hour + 10, total_tokens=2))
            self.record(self.event(occurred_at=old_hour + 150, total_tokens=3))
            self.store.cleanup_old_events(query_now)
            overview = self.store.get_overview("alice", StatsFilters(
                start_time=old_hour + 100,
                end_time=old_hour + 200,
                granularity="hour",
            ))

        self.assertEqual(overview["totals"]["request_count"], 2)
        self.assertEqual(overview["totals"]["total_tokens"], 5)
        self.assertEqual(
            overview["data_quality"]["boundary_precision"],
            "hourly_approximate",
        )

    def test_old_fractional_timezone_period_is_conservatively_marked_approximate(self):
        query_now = self.now + 1800
        zone = ZoneInfo("Asia/Kathmandu")
        old_local = datetime.fromtimestamp(
            query_now - (DETAIL_RETENTION_DAYS + 1) * 86400,
            zone,
        )
        local_midnight = old_local.replace(hour=0, minute=0, second=0, microsecond=0)
        boundary = int(local_midnight.timestamp())
        start = boundary // 3600 * 3600 - 3600
        end = (boundary // 3600 + 2) * 3600

        with mock.patch("src.usage_stats_store.time.time", return_value=query_now):
            self.record(self.event(occurred_at=boundary - 1, total_tokens=2))
            self.record(self.event(occurred_at=boundary, total_tokens=3))
            self.store.cleanup_old_events(query_now)
            overview = self.store.get_overview("alice", StatsFilters(
                start_time=start,
                end_time=end,
                timezone="Asia/Kathmandu",
                granularity="day",
            ))

        self.assertEqual(overview["totals"]["request_count"], 2)
        self.assertEqual(
            overview["data_quality"]["boundary_precision"],
            "hourly_approximate",
        )

    def test_old_range_checks_fractional_offsets_before_the_retention_cutoff(self):
        query_now = int(datetime(2025, 1, 15, tzinfo=timezone.utc).timestamp())
        old_start = int(datetime(2024, 9, 1, tzinfo=timezone.utc).timestamp())
        old_end = query_now - DETAIL_RETENTION_DAYS * 86400

        with mock.patch("src.usage_stats_store.time.time", return_value=query_now):
            overview = self.store.get_overview("alice", StatsFilters(
                start_time=old_start,
                end_time=old_end,
                timezone="Australia/Lord_Howe",
                granularity="day",
            ))

        self.assertEqual(
            overview["data_quality"]["boundary_precision"],
            "hourly_approximate",
        )

    def test_overview_reads_hourly_histograms_and_details_in_one_snapshot(self):
        self.record(self.event(occurred_at=self.now + 100))
        real_database = SQLiteDatabase(self.database_path)
        statements = []

        @contextmanager
        def recording_connect(*, create=True):
            with real_database.connect(create=create) as connection:
                class ConnectionProxy:
                    def execute(self, statement, parameters=()):
                        statements.append(statement.strip())
                        return connection.execute(statement, parameters)

                    def executemany(self, statement, parameters):
                        statements.append(statement.strip())
                        return connection.executemany(statement, parameters)

                yield ConnectionProxy()

        database = SimpleNamespace(path=self.database_path, connect=recording_connect)
        with mock.patch.object(self.store, "_database", return_value=database):
            overview = self.store.get_overview("alice", StatsFilters(
                start_time=self.now + 50,
                end_time=self.now + 150,
            ))

        self.assertEqual(overview["totals"]["request_count"], 1)
        self.assertEqual(statements[0], "BEGIN")

    def test_overview_never_fetches_raw_hourly_or_histogram_rows(self):
        self.record(self.event())
        real_database = SQLiteDatabase(self.database_path)

        @contextmanager
        def guarded_connect(*, create=True):
            with real_database.connect(create=create) as connection:
                class ConnectionProxy:
                    def execute(self, statement, parameters=()):
                        normalized = " ".join(statement.split()).lower()
                        if normalized.startswith("select * from usage_hourly"):
                            raise AssertionError("raw hourly fetch is forbidden")
                        return connection.execute(statement, parameters)

                    def executemany(self, statement, parameters):
                        return connection.executemany(statement, parameters)

                yield ConnectionProxy()

        database = SimpleNamespace(path=self.database_path, connect=guarded_connect)
        with mock.patch.object(self.store, "_database", return_value=database):
            overview = self.store.get_overview("alice", StatsFilters(
                start_time=self.now,
                end_time=self.now + 3600,
            ))

        self.assertEqual(overview["totals"]["request_count"], 1)

    def test_overview_filters_user_time_traffic_and_dimensions(self):
        event_id = self.record(self.event())
        self.record(self.event(source="admin_playground", occurred_at=self.now + 1))
        self.record(self.event(source="credential_test", occurred_at=self.now + 2))
        self.record(self.event(outcome="failure", occurred_at=self.now + 3600))
        self.record(self.event(occurred_at=self.now + 7200))
        self.record(self.event(occurred_at=self.now, api_key_id="key-2"), username="bob")

        base = {
            "start_time": self.now,
            "end_time": self.now + 7200,
            "timezone": "UTC",
        }
        self.assertEqual(
            self.store.get_overview("alice", {**base, "traffic": "external"})["totals"]["request_count"],
            2,
        )
        self.assertEqual(
            self.store.get_overview("alice", {**base, "traffic": "admin"})["totals"]["request_count"],
            2,
        )
        exact = self.store.get_overview("alice", {
            **base,
            "model": "glm-5.2",
            "api_key_id": "key-1",
            "credential_id": "credential-1",
            "outcome": "success",
        })
        self.assertEqual(exact["totals"]["request_count"], 3)
        self.assertIsNotNone(self.store.get_event("alice", event_id))
        self.assertIsNone(self.store.get_event("bob", event_id))

    def test_day_and_week_series_use_iana_timezone_and_dst_boundaries(self):
        # 2024-11-03 05:00/06:00 UTC 是纽约夏令时回拨后的两个本地 01:00。
        first = 1730610000
        second = 1730613600
        self.record(self.event(occurred_at=first))
        self.record(self.event(occurred_at=second))

        hourly = self.store.get_overview("alice", StatsFilters(
            start_time=first,
            end_time=second + 3600,
            timezone="America/New_York",
            granularity="hour",
        ))["series"]
        daily = self.store.get_overview("alice", StatsFilters(
            start_time=first,
            end_time=second + 3600,
            timezone="America/New_York",
            granularity="day",
        ))["series"]
        weekly = self.store.get_overview("alice", StatsFilters(
            start_time=first,
            end_time=second + 3600,
            timezone="America/New_York",
            granularity="week",
        ))["series"]

        self.assertEqual(len(hourly), 2)
        self.assertNotEqual(hourly[0]["period"], hourly[1]["period"])
        self.assertEqual(daily[0]["request_count"], 2)
        self.assertEqual(weekly[0]["request_count"], 2)
        self.assertEqual(daily[0]["period"], "2024-11-03")
        self.assertEqual(weekly[0]["period"], "2024-10-28")

    def test_auto_granularity_and_empty_overview(self):
        empty = self.store.get_overview("alice", StatsFilters(
            start_time=self.now,
            end_time=self.now + 3600,
            timezone="UTC",
        ))
        self.assertEqual(empty["totals"], {
            "request_count": 0,
            "success_rate": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cache_hit_tokens": None,
            "cache_miss_tokens": None,
            "total_credit": 0,
            "p95_first_output_ms": None,
            "p95_first_output_ms_overflow": False,
            "p95_total_ms": None,
            "p95_total_ms_overflow": False,
            "usage_coverage": None,
        })
        self.assertEqual(empty["series"], [])

        self.record(self.event())
        short = self.store.get_overview("alice", StatsFilters(
            start_time=self.now,
            end_time=self.now + 3600,
            timezone="UTC",
            granularity="auto",
        ))["series"]
        long = self.store.get_overview("alice", StatsFilters(
            start_time=self.now - 200 * 86400,
            end_time=self.now + 3600,
            timezone="UTC",
            granularity="auto",
        ))["series"]
        self.assertIn("T", short[0]["period"])
        self.assertNotIn("T", long[0]["period"])

    def test_list_events_uses_stable_numbered_pages_and_filters(self):
        ids = [
            self.record(self.event(occurred_at=self.now + offset))
            for offset in range(3)
        ]
        self.record(self.event(source="admin_playground", occurred_at=self.now + 4))

        filters = StatsFilters(
            start_time=self.now,
            end_time=self.now + 5,
            traffic="external",
        )
        with mock.patch("src.usage_stats_store.time.time", return_value=self.now + 10):
            first_page = self.store.list_events("alice", filters, page=1, page_size=2)

        self.record(self.event(occurred_at=self.now + 3))
        second_page = self.store.list_events(
            "alice",
            {
                "start_time": self.now,
                "end_time": self.now + 5,
                "traffic": "external",
            },
            page=2,
            page_size=2,
            snapshot_id=first_page["snapshot_id"],
            snapshot_time=first_page["snapshot_time"],
        )

        self.assertEqual([item["id"] for item in first_page["items"]], ids[::-1][:2])
        self.assertEqual([item["id"] for item in second_page["items"]], [ids[0]])
        self.assertEqual(first_page["page"], 1)
        self.assertEqual(first_page["page_size"], 2)
        self.assertEqual(first_page["total"], 3)
        self.assertEqual(first_page["total_pages"], 2)
        self.assertEqual(second_page["total"], 3)
        self.assertEqual(second_page["total_pages"], 2)
        self.assertEqual(second_page["snapshot_id"], first_page["snapshot_id"])
        self.assertEqual(second_page["snapshot_time"], self.now + 10)
        detail = self.store.get_event("alice", ids[0])
        self.assertIs(detail["client_stream"], True)
        self.assertEqual(detail["total_tokens"], 16)
        self.assertEqual(detail["started_at"], self.now)
        self.assertNotIn("username", detail)
        self.assertNotIn("occurred_at", detail)

    def test_list_events_validates_pages_and_snapshot_pair(self):
        invalid_calls = (
            {"page": 0},
            {"page": True},
            {"page_size": 0},
            {"page_size": 101},
            {"snapshot_id": -1, "snapshot_time": self.now},
            {"snapshot_id": SQLITE_MAX_INTEGER + 1, "snapshot_time": self.now},
            {"snapshot_id": 0},
            {"snapshot_time": self.now},
            {"page": SQLITE_MAX_INTEGER, "page_size": 100},
        )
        for kwargs in invalid_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.list_events("alice", StatsFilters(), **kwargs)

    def test_list_events_returns_empty_out_of_range_and_empty_snapshot(self):
        event_id = self.record(self.event())

        out_of_range = self.store.list_events(
            "alice", StatsFilters(), page=2, page_size=1
        )
        empty_snapshot = self.store.list_events(
            "alice",
            StatsFilters(),
            snapshot_id=0,
            snapshot_time=self.now,
        )

        self.assertEqual(out_of_range["items"], [])
        self.assertEqual(out_of_range["total"], 1)
        self.assertEqual(out_of_range["total_pages"], 1)
        self.assertEqual(out_of_range["snapshot_id"], event_id)
        self.assertEqual(empty_snapshot["items"], [])
        self.assertEqual(empty_snapshot["total"], 0)
        self.assertEqual(empty_snapshot["total_pages"], 0)

    def test_list_events_allows_old_snapshot_before_cleanup_advances(self):
        old_snapshot_time = self.now - 60
        event_id = self.record(self.event(
            occurred_at=old_snapshot_time - DETAIL_RETENTION_DAYS * 86400,
        ))
        self.store.cleanup_old_events(old_snapshot_time)

        page = self.store.list_events(
            "alice",
            snapshot_id=event_id,
            snapshot_time=old_snapshot_time,
        )

        self.assertEqual([item["id"] for item in page["items"]], [event_id])
        detail = self.store.get_event(
            "alice",
            event_id,
            snapshot_id=page["snapshot_id"],
            snapshot_time=page["snapshot_time"],
        )
        self.assertEqual(detail["id"], event_id)

    def test_list_events_rejects_snapshot_invalidated_by_cleanup(self):
        cutoff = self.now - DETAIL_RETENTION_DAYS * 86400
        event_id = self.record(self.event(occurred_at=cutoff))
        with mock.patch("src.usage_stats_store.time.time", return_value=self.now):
            first_page = self.store.list_events("alice")
        self.assertEqual([item["id"] for item in first_page["items"]], [event_id])

        self.store.cleanup_old_events(self.now + 1)

        with self.assertRaisesRegex(ValueError, "snapshot is no longer valid"):
            self.store.list_events(
                "alice",
                snapshot_id=first_page["snapshot_id"],
                snapshot_time=first_page["snapshot_time"],
            )
        with self.assertRaisesRegex(ValueError, "snapshot is no longer valid"):
            self.store.get_event(
                "alice",
                event_id,
                snapshot_id=first_page["snapshot_id"],
                snapshot_time=first_page["snapshot_time"],
            )

    def test_list_events_rejects_snapshot_beyond_event_sequence(self):
        event_id = self.record(self.event())

        with self.assertRaisesRegex(ValueError, "snapshot is no longer valid"):
            self.store.list_events(
                "alice",
                snapshot_id=event_id + 1,
                snapshot_time=self.now,
            )
        with self.assertRaisesRegex(ValueError, "snapshot is no longer valid"):
            self.store.get_event(
                "alice",
                event_id,
                snapshot_id=event_id + 1,
                snapshot_time=self.now,
            )

    def test_get_event_requires_complete_valid_snapshot_pair(self):
        event_id = self.record(self.event())
        invalid_snapshots = (
            {"snapshot_id": event_id},
            {"snapshot_time": self.now},
            {"snapshot_id": True, "snapshot_time": self.now},
            {"snapshot_id": event_id, "snapshot_time": True},
            {"snapshot_id": SQLITE_MAX_INTEGER + 1, "snapshot_time": self.now},
            {"snapshot_id": event_id, "snapshot_time": MAX_STATS_TIMESTAMP + 1},
        )
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot=snapshot), self.assertRaisesRegex(
                ValueError,
                "pagination snapshot",
            ):
                self.store.get_event("alice", event_id, **snapshot)

    def test_cleanup_removes_only_expired_details_and_keeps_aggregates(self):
        cutoff = self.now - DETAIL_RETENTION_DAYS * 86400
        old_id = self.record(self.event(occurred_at=cutoff - 1))
        boundary_id = self.record(self.event(occurred_at=cutoff))

        deleted = self.store.cleanup_old_events(self.now)

        self.assertEqual(deleted, 1)
        self.assertIsNone(self.store.get_event("alice", old_id))
        with mock.patch("src.usage_stats_store.time.time", return_value=self.now):
            self.assertIsNotNone(self.store.get_event("alice", boundary_id))
        overview = self.store.get_overview("alice", StatsFilters(
            start_time=cutoff - 3600,
            end_time=cutoff + 3600,
        ))
        self.assertEqual(overview["totals"]["request_count"], 2)

    def test_recording_never_runs_retention_cleanup_in_the_write_transaction(self):
        with mock.patch.object(self.store, "cleanup_old_events") as cleanup:
            first_id = self.record(self.event())
            second_id = self.record(self.event(occurred_at=self.now + 1))

        self.assertIsInstance(first_id, int)
        self.assertIsInstance(second_id, int)
        cleanup.assert_not_called()

    def test_cleanup_deletes_expired_details_in_bounded_transactions(self):
        cutoff = self.now - DETAIL_RETENTION_DAYS * 86400
        expired_count = CLEANUP_BATCH_SIZE + 3
        with SQLiteDatabase(self.database_path).connect() as connection:
            connection.executemany(
                "INSERT INTO usage_events(username, occurred_at, source, requested_model, outcome) "
                "VALUES ('alice', ?, 'external_api', 'glm-5.2', 'success')",
                [(cutoff - index - 1,) for index in range(expired_count)],
            )
        real_database = SQLiteDatabase(self.database_path)
        connection_count = 0

        @contextmanager
        def counting_connect(*, create=True):
            nonlocal connection_count
            connection_count += 1
            with real_database.connect(create=create) as connection:
                yield connection

        database = SimpleNamespace(path=self.database_path, connect=counting_connect)
        with mock.patch.object(self.store, "_database", return_value=database):
            deleted = self.store.cleanup_old_events(self.now)

        self.assertEqual(deleted, expired_count)
        self.assertEqual(connection_count, 2)

    def test_detail_queries_hide_expired_rows_before_lazy_cleanup_is_due(self):
        expired_at = self.now - DETAIL_RETENTION_DAYS * 86400 - 1
        with mock.patch("src.usage_stats_store.time.time", return_value=self.now):
            self.record(self.event())
            expired_id = self.record(self.event(occurred_at=expired_at))
            with SQLiteDatabase(self.database_path).connect() as connection:
                physically_present = connection.execute(
                    "SELECT 1 FROM usage_events WHERE id = ?", (expired_id,)
                ).fetchone()
            listed = self.store.list_events("alice")
            detail = self.store.get_event("alice", expired_id)

        self.assertIsNotNone(physically_present)
        self.assertNotIn(expired_id, [item["id"] for item in listed["items"]])
        self.assertIsNone(detail)

    def test_record_failure_is_logged_and_counted_without_raising(self):
        with mock.patch.object(
            self.store,
            "_record_event",
            side_effect=sqlite3.OperationalError("disk full"),
        ), mock.patch("src.usage_stats_store.logger.exception") as log:
            self.assertIsNone(self.record(self.event()))

        overview = self.store.get_overview("alice", StatsFilters())
        self.assertEqual(overview["data_quality"]["dropped_events"], 1)
        self.assertEqual(self.store.get_dropped_events("alice"), 1)
        self.assertEqual(self.store.get_dropped_events("bob"), 0)
        log.assert_called_once()
        self.store.reset_dropped_events_for_tests()
        self.assertEqual(self.store.dropped_events, 0)

    def test_invalid_events_and_filters_fail_fast_inside_their_public_boundaries(self):
        invalid_events = [
            self.event(source="unknown"),
            self.event(outcome="unknown"),
            self.event(requested_model="  "),
        ]
        for event in invalid_events:
            with self.subTest(event=event):
                self.assertIsNone(self.record(event))
        self.assertEqual(self.store.dropped_events, 3)

        invalid_filters = [
            StatsFilters(traffic="invalid"),
            StatsFilters(granularity="minute"),
            StatsFilters(timezone="Mars/Olympus"),
            StatsFilters(start_time=2, end_time=1),
        ]
        for filters in invalid_filters:
            with self.subTest(filters=filters):
                with self.assertRaises(ValueError):
                    self.store.get_overview("alice", filters)

    def test_store_without_explicit_path_resolves_current_config_path(self):
        store = UsageStatsStore()
        with mock.patch(
            "src.usage_stats_store.get_database_path",
            return_value=self.database_path,
        ) as resolver:
            event_id = store.record_event(self.event(), username="alice")

        self.assertIsInstance(event_id, int)
        resolver.assert_called_once_with()

    def test_edge_validation_and_empty_store_paths_are_explicit(self):
        missing_store = UsageStatsStore(Path(self._temp_dir.name) / "missing.sqlite3")
        for operation in (
            lambda: missing_store.get_overview("alice"),
            lambda: missing_store.list_events("alice"),
            lambda: missing_store.get_event("alice", 1),
            lambda: missing_store.cleanup_old_events(),
        ):
            with self.subTest(operation=operation), self.assertRaises(FileNotFoundError):
                operation()

        with self.assertRaisesRegex(TypeError, "filters"):
            self.store.get_overview("alice", object())
        with self.assertRaisesRegex(ValueError, "outcome"):
            self.store.get_overview("alice", StatsFilters(outcome="invalid"))
        with self.assertRaisesRegex(ValueError, "start_time"):
            self.store.get_overview("alice", StatsFilters(start_time=-1))
        with self.assertRaisesRegex(ValueError, "end_time"):
            self.store.get_overview("alice", StatsFilters(end_time=True))
        with self.assertRaisesRegex(ValueError, "start_time"):
            self.store.get_overview("alice", StatsFilters(start_time=MAX_STATS_TIMESTAMP + 1))
        with self.assertRaisesRegex(ValueError, "end_time"):
            self.store.get_overview("alice", StatsFilters(end_time=MAX_STATS_TIMESTAMP + 1))
        with self.assertRaisesRegex(ValueError, "username"):
            self.store.get_overview(" ", StatsFilters())
        with self.assertRaisesRegex(ValueError, "page"):
            self.store.list_events("alice", page=True)
        with self.assertRaisesRegex(ValueError, "event_id"):
            self.store.get_event("alice", True)
        with self.assertRaisesRegex(ValueError, "now"):
            self.store.cleanup_old_events("invalid")

        with mock.patch("src.usage_stats_store.logger.exception"):
            self.assertIsNone(self.store.record_event("invalid", username="alice"))
            self.assertIsNone(self.record(self.event(occurred_at=-1)))
            self.assertIsNone(self.record(self.event(client_stream=1)))
            self.assertIsNone(self.store.record_event(self.event(), username=" "))
        self.assertEqual(self.store.get_dropped_events("alice"), 3)
        self.assertEqual(self.store.get_dropped_events("bob"), 0)
        self.assertEqual(self.store.dropped_events, 4)
        with self.assertRaisesRegex(ValueError, "username"):
            self.store.get_dropped_events(" ")

    def test_period_helpers_reject_excessive_series_and_skip_irrelevant_boundaries(self):
        zone = ZoneInfo("UTC")
        self.assertFalse(
            self.store._has_fractional_period_boundary(10, 10, zone, "day")
        )
        self.assertFalse(
            self.store._has_fractional_period_boundary(0, 10, zone, "hour")
        )
        with self.assertRaisesRegex(ValueError, "coarser granularity"):
            self.store._series_periods(
                StatsFilters(
                    start_time=1,
                    end_time=(MAX_SERIES_POINTS + 2) * 3600,
                ),
                zone,
                "hour",
                1,
                (MAX_SERIES_POINTS + 2) * 3600 - 1,
            )

    def test_unbounded_auto_overview_and_nullable_identity_fields(self):
        self.record(self.event(
            occurred_at=self.now - 3 * 86400,
            api_key_id=None,
            api_key_name=None,
            credential_id=None,
            credential_label=None,
            client_stream=None,
        ))
        current_id = self.record(self.event(
            occurred_at=self.now,
            api_key_id=None,
            api_key_name=None,
            credential_id=None,
            credential_label=None,
            client_stream=None,
        ))

        overview = self.store.get_overview("alice", None)
        self.assertEqual(len(overview["series"]), 4)
        self.assertNotIn("T", overview["series"][0]["period"])
        self.assertEqual(overview["dimensions"]["api_keys"], [])
        self.assertEqual(overview["dimensions"]["credentials"], [])
        detail = self.store.get_event("alice", current_id)
        self.assertIsNone(detail["client_stream"])
        with self.assertRaisesRegex(ValueError, "dropped_events"):
            self.store.get_overview("alice", StatsFilters(), dropped_events=-1)


class UsageStatsRetentionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_cleanup_runs_off_loop_and_periodic_cleanup_stops_cleanly(self):
        event_loop_thread = threading.get_ident()

        class Store:
            def __init__(self):
                self.calls = []

            def cleanup_old_events(self):
                self.calls.append(threading.get_ident())

        store = Store()
        manager = UsageStatsRetentionManager(
            store,
            interval_seconds=0.01,
            retry_seconds=0.005,
        )

        await manager.startup()
        for _ in range(100):
            if len(store.calls) >= 2:
                break
            await asyncio.sleep(0.005)
        await manager.shutdown()
        calls_after_shutdown = len(store.calls)
        await asyncio.sleep(0.02)

        self.assertGreaterEqual(calls_after_shutdown, 2)
        self.assertTrue(all(thread_id != event_loop_thread for thread_id in store.calls))
        self.assertEqual(len(store.calls), calls_after_shutdown)

    async def test_lifecycle_guards_allow_inactive_shutdown_and_reject_double_start(self):
        store = mock.Mock()
        manager = UsageStatsRetentionManager(store, interval_seconds=60)

        await manager.shutdown()
        await manager._run()
        await manager.startup()
        with self.assertRaisesRegex(RuntimeError, "already running"):
            await manager.startup()
        await manager.shutdown()

        store.cleanup_old_events.assert_called_once_with()

    async def test_periodic_cleanup_failure_uses_retry_delay_then_recovers(self):
        class Store:
            def __init__(self):
                self.calls = 0

            def cleanup_old_events(self):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("database busy")

        store = Store()
        manager = UsageStatsRetentionManager(
            store,
            interval_seconds=0.01,
            retry_seconds=0.001,
        )

        with self.assertLogs("src.usage_stats_store", level="ERROR"):
            await manager.startup()
            for _ in range(100):
                if store.calls >= 3:
                    break
                await asyncio.sleep(0.002)
            await manager.shutdown()

        self.assertGreaterEqual(store.calls, 3)


if __name__ == "__main__":
    unittest.main()
