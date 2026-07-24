"""脱敏请求用量的持久化记录、汇总与查询。"""
import asyncio
import base64
import bisect
import hashlib
import json
import logging
import math
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import get_database_path
from .sqlite_database import SQLiteDatabase

logger = logging.getLogger(__name__)

DETAIL_RETENTION_DAYS = 90
CLEANUP_BATCH_SIZE = 1000
MAX_STATS_TIMESTAMP = 253_370_764_799
SQLITE_MAX_INTEGER = 9_223_372_036_854_775_807
BREAKDOWN_LIMIT = 20
MAX_SERIES_POINTS = 4096
LATENCY_BUCKET_UPPER_BOUNDS_MS = (
    50,
    100,
    250,
    500,
    1_000,
    2_000,
    5_000,
    10_000,
    30_000,
    60_000,
    120_000,
    300_000,
    600_000,
)

_SOURCES = frozenset({"external_api", "admin_playground", "credential_test"})
_OUTCOMES = frozenset({"success", "failure", "cancelled"})
_TRAFFIC_VALUES = frozenset({"all", "external", "admin"})
_GRANULARITIES = frozenset({"auto", "hour", "day", "week"})
_UNATTRIBUTED_DROPS = "\0"
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "reasoning_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
    "cache_write_tokens",
)
_HOURLY_SUM_FIELDS = _TOKEN_FIELDS + (
    "credit",
    "request_bytes",
    "response_bytes",
    "retry_count",
    "tool_call_count",
)
_OVERVIEW_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
)


@dataclass(frozen=True)
class TokenUsage:
    """从上游 usage 中提取的规范化计量字段。"""

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    credit: Optional[float] = None


@dataclass(frozen=True)
class UsageEvent:
    """一次请求的脱敏统计事件；不接收任何请求或响应原文。"""

    source: str
    requested_model: str
    occurred_at: int = field(default_factory=lambda: int(time.time()))
    upstream_model: Optional[str] = None
    api_key_id: Optional[str] = None
    api_key_name: Optional[str] = None
    credential_id: Optional[str] = None
    credential_label: Optional[str] = None
    outcome: str = "success"
    http_status: Optional[int] = None
    result_status: Optional[int] = None
    error_type: Optional[str] = None
    client_stream: Optional[bool] = None
    thinking_mode: Optional[str] = None
    message_count: Optional[int] = None
    tool_count: Optional[int] = None
    request_bytes: Optional[int] = None
    response_bytes: Optional[int] = None
    retry_count: Optional[int] = None
    tool_call_count: Optional[int] = None
    finish_reason: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cache_hit_tokens: Optional[int] = None
    cache_miss_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    credit: Optional[float] = None
    duration_ms: Optional[float] = None
    first_event_ms: Optional[float] = None
    first_output_ms: Optional[float] = None
    first_reasoning_ms: Optional[float] = None
    first_content_ms: Optional[float] = None


@dataclass(frozen=True)
class StatsFilters:
    """统计查询筛选器，时间区间采用 ``[start_time, end_time)``。"""

    start_time: Optional[int] = None
    end_time: Optional[int] = None
    traffic: str = "all"
    model: Optional[str] = None
    api_key_id: Optional[str] = None
    credential_id: Optional[str] = None
    outcome: Optional[str] = None
    timezone: str = "UTC"
    granularity: str = "auto"


@dataclass(frozen=True)
class PercentileEstimate:
    """固定桶分位数估计；overflow 表示数值只是严格下界。"""

    value_ms: Optional[int]
    overflow: bool


def _nonnegative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or int(value) != value:
        return None
    return int(value)


def _nonnegative_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        return None
    return converted


def _nested_value(data: Mapping[str, Any], *path: str) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _first_int(data: Mapping[str, Any], paths: Tuple[Tuple[str, ...], ...]) -> Optional[int]:
    for path in paths:
        value = _nonnegative_int(_nested_value(data, *path))
        if value is not None:
            return value
    return None


def normalize_usage(usage: Any) -> TokenUsage:
    """宽松提取 OpenAI 与 CodeBuddy usage，缺失或畸形字段保持 ``None``。"""
    if not isinstance(usage, Mapping):
        return TokenUsage()
    return TokenUsage(
        input_tokens=_first_int(usage, (("prompt_tokens",), ("input_tokens",))),
        output_tokens=_first_int(usage, (("completion_tokens",), ("output_tokens",))),
        total_tokens=_first_int(usage, (("total_tokens",),)),
        reasoning_tokens=_first_int(usage, (
            ("reasoning_tokens",),
            ("completion_tokens_details", "reasoning_tokens"),
            ("completion_thinking_tokens",),
        )),
        cache_hit_tokens=_first_int(usage, (
            ("prompt_cache_hit_tokens",),
            ("cache_read_input_tokens",),
            ("prompt_tokens_details", "cached_tokens"),
            ("cached_tokens",),
        )),
        cache_miss_tokens=_first_int(usage, (("prompt_cache_miss_tokens",),)),
        cache_write_tokens=_first_int(usage, (
            ("prompt_cache_write_tokens",),
            ("cache_creation_input_tokens",),
        )),
        credit=_nonnegative_float(usage.get("credit")),
    )


def latency_bucket_index(value: Any) -> Optional[int]:
    """返回严格上界固定桶序号；达到最大边界时使用独立溢出桶。"""
    normalized = _nonnegative_float(value)
    if normalized is None:
        return None
    return bisect.bisect_right(LATENCY_BUCKET_UPPER_BOUNDS_MS, normalized)


def approximate_percentile(
        counts: Mapping[int, int],
        percentile: float,
) -> PercentileEstimate:
    """用非累计固定桶按 nearest-rank 规则估算分位数。"""
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in the range (0, 1]")
    total = sum(max(0, int(value)) for value in counts.values())
    if total == 0:
        return PercentileEstimate(None, False)
    target = math.ceil(total * percentile)
    seen = 0
    for index, upper_bound in enumerate(LATENCY_BUCKET_UPPER_BOUNDS_MS):
        seen += max(0, int(counts.get(index, 0)))
        if seen >= target:
            return PercentileEstimate(upper_bound, False)
    return PercentileEstimate(LATENCY_BUCKET_UPPER_BOUNDS_MS[-1], True)


def _clean_optional_text(value: Any, maximum: int = 200) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned[:maximum] or None


def _normalize_event(event: UsageEvent) -> UsageEvent:
    if not isinstance(event, UsageEvent):
        raise TypeError("event must be a UsageEvent")
    source = str(event.source).strip()
    if source not in _SOURCES:
        raise ValueError(f"Unsupported usage event source: {source}")
    outcome = str(event.outcome).strip()
    if outcome not in _OUTCOMES:
        raise ValueError(f"Unsupported usage event outcome: {outcome}")
    requested_model = _clean_optional_text(event.requested_model)
    if requested_model is None:
        raise ValueError("requested_model must not be empty")
    occurred_at = _nonnegative_int(event.occurred_at)
    if occurred_at is None:
        raise ValueError("occurred_at must be a non-negative integer")
    if event.client_stream is not None and not isinstance(event.client_stream, bool):
        raise ValueError("client_stream must be a boolean or None")

    integer_fields = (
        "http_status",
        "result_status",
        "message_count",
        "tool_count",
        "request_bytes",
        "response_bytes",
        "retry_count",
        "tool_call_count",
    ) + _TOKEN_FIELDS
    values = {
        name: _nonnegative_int(getattr(event, name))
        for name in integer_fields
    }
    float_fields = (
        "credit",
        "duration_ms",
        "first_event_ms",
        "first_output_ms",
        "first_reasoning_ms",
        "first_content_ms",
    )
    values.update({
        name: _nonnegative_float(getattr(event, name))
        for name in float_fields
    })
    values.update({
        "source": source,
        "requested_model": requested_model,
        "occurred_at": occurred_at,
        "upstream_model": _clean_optional_text(event.upstream_model),
        "api_key_id": _clean_optional_text(event.api_key_id),
        "api_key_name": _clean_optional_text(event.api_key_name),
        "credential_id": _clean_optional_text(event.credential_id),
        "credential_label": _clean_optional_text(event.credential_label),
        "outcome": outcome,
        "error_type": _clean_optional_text(event.error_type, 120),
        "thinking_mode": _clean_optional_text(event.thinking_mode, 80),
        "finish_reason": _clean_optional_text(event.finish_reason, 80),
    })
    return replace(event, **values)


def _coerce_filters(filters: Union[StatsFilters, Mapping[str, Any], None]) -> Tuple[StatsFilters, ZoneInfo]:
    if filters is None:
        filters = StatsFilters()
    elif isinstance(filters, Mapping):
        filters = StatsFilters(**dict(filters))
    elif not isinstance(filters, StatsFilters):
        raise TypeError("filters must be StatsFilters, a mapping, or None")

    if filters.traffic not in _TRAFFIC_VALUES:
        raise ValueError(f"Unsupported traffic filter: {filters.traffic}")
    if filters.granularity not in _GRANULARITIES:
        raise ValueError(f"Unsupported granularity: {filters.granularity}")
    if filters.outcome is not None and filters.outcome not in _OUTCOMES:
        raise ValueError(f"Unsupported outcome filter: {filters.outcome}")

    start_time = _nonnegative_int(filters.start_time)
    end_time = _nonnegative_int(filters.end_time)
    if filters.start_time is not None and start_time is None:
        raise ValueError("start_time must be a non-negative integer or None")
    if filters.end_time is not None and end_time is None:
        raise ValueError("end_time must be a non-negative integer or None")
    if start_time is not None and start_time > MAX_STATS_TIMESTAMP:
        raise ValueError(f"start_time must not exceed {MAX_STATS_TIMESTAMP}")
    if end_time is not None and end_time > MAX_STATS_TIMESTAMP:
        raise ValueError(f"end_time must not exceed {MAX_STATS_TIMESTAMP}")
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise ValueError("start_time must be earlier than end_time")
    try:
        zone = ZoneInfo(filters.timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ValueError(f"Unknown IANA timezone: {filters.timezone}") from error

    normalized = replace(
        filters,
        start_time=start_time,
        end_time=end_time,
        model=_clean_optional_text(filters.model),
        api_key_id=_clean_optional_text(filters.api_key_id),
        credential_id=_clean_optional_text(filters.credential_id),
    )
    return normalized, zone


def _validate_username(username: Any) -> str:
    normalized = str(username).strip()
    if not normalized:
        raise ValueError("username must not be empty")
    return normalized


class UsageStatsStore:
    """将明细与小时汇总原子写入 SQLite，并按系统用户隔离查询。"""

    def __init__(self, database_path: Union[str, Path, None] = None):
        self._database_path = Path(database_path) if database_path is not None else None
        self._dropped_events: Dict[str, int] = defaultdict(int)
        self._dropped_lock = threading.Lock()

    def _database(self) -> SQLiteDatabase:
        path = self._database_path if self._database_path is not None else get_database_path()
        return SQLiteDatabase(path)

    @property
    def dropped_events(self) -> int:
        with self._dropped_lock:
            return sum(self._dropped_events.values())

    def get_dropped_events(self, username: str) -> int:
        """返回指定系统用户在当前进程内的统计丢失数。"""
        normalized = _validate_username(username)
        with self._dropped_lock:
            return self._dropped_events.get(normalized, 0)

    def reset_dropped_events_for_tests(self) -> None:
        with self._dropped_lock:
            self._dropped_events.clear()

    def record_event(self, event: UsageEvent, *, username: str) -> Optional[int]:
        """记录事件；存储异常只记日志和丢失计数，不影响原请求。"""
        drop_owner = str(username).strip() or _UNATTRIBUTED_DROPS
        try:
            return self._record_event(_normalize_event(event), _validate_username(username))
        except Exception:
            with self._dropped_lock:
                self._dropped_events[drop_owner] += 1
            logger.exception("Failed to persist usage statistics event")
            return None

    def _record_event(self, event: UsageEvent, username: str) -> int:
        detail = asdict(event)
        event_columns = tuple(detail)
        detail["username"] = username
        detail["client_stream"] = (
            None if event.client_stream is None else int(event.client_stream)
        )
        columns = ("username",) + event_columns
        placeholders = ", ".join(f":{name}" for name in columns)
        with self._database().connect(create=False) as connection:
            cursor = connection.execute(
                f"INSERT INTO usage_events ({', '.join(columns)}) VALUES ({placeholders})",
                detail,
            )
            event_id = int(cursor.lastrowid)
            hourly_id = self._upsert_hourly(connection, username, event)
            if event.outcome == "success":
                self._record_latency(connection, hourly_id, "total", event.duration_ms)
                self._record_latency(
                    connection, hourly_id, "first_output", event.first_output_ms
                )
        return event_id

    @staticmethod
    def _cleanup_in_connection(connection, now: int) -> int:
        cutoff = now - DETAIL_RETENTION_DAYS * 86400
        cursor = connection.execute(
            "DELETE FROM usage_events WHERE id IN ("
            "SELECT id FROM usage_events WHERE occurred_at < ? "
            "ORDER BY occurred_at, id LIMIT ?)",
            (cutoff, CLEANUP_BATCH_SIZE),
        )
        deleted = cursor.rowcount
        connection.execute(
            "INSERT INTO usage_retention_state(id, detail_cutoff) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET detail_cutoff = "
            "MAX(detail_cutoff, excluded.detail_cutoff)",
            (cutoff,),
        )
        return deleted

    @staticmethod
    def _hourly_values(username: str, event: UsageEvent) -> Dict[str, Any]:
        """把明细事件转换为小时汇总行使用的维度和计数器。"""
        values: Dict[str, Any] = {
            "username": username,
            "hour": event.occurred_at // 3600 * 3600,
            "source": event.source,
            "model": event.upstream_model or event.requested_model,
            "api_key_id": event.api_key_id or "",
            "api_key_name": event.api_key_name or "",
            "credential_id": event.credential_id or "",
            "credential_label": event.credential_label or "",
            "outcome": event.outcome,
            "request_count": 1,
            "success_count": int(event.outcome == "success"),
            "failure_count": int(event.outcome == "failure"),
            "cancelled_count": int(event.outcome == "cancelled"),
            "usage_known_count": int(event.total_tokens is not None),
        }
        for name in _HOURLY_SUM_FIELDS:
            value = getattr(event, name)
            values[f"{name}_sum"] = value if value is not None else 0
            values[f"{name}_known_count"] = int(value is not None)
        return values

    @classmethod
    def _upsert_hourly(cls, connection, username: str, event: UsageEvent) -> int:
        values = cls._hourly_values(username, event)

        dimensions = (
            "username", "hour", "source", "model", "api_key_id", "api_key_name",
            "credential_id", "credential_label", "outcome",
        )
        counters = tuple(name for name in values if name not in dimensions)
        columns = dimensions + counters
        connection.execute(
            f"""
            INSERT INTO usage_hourly ({', '.join(columns)})
            VALUES ({', '.join(f':{name}' for name in columns)})
            ON CONFLICT (
                username, hour, source, model, api_key_id, api_key_name,
                credential_id, credential_label, outcome
            ) DO UPDATE SET
                {', '.join(f'{name} = {name} + excluded.{name}' for name in counters)}
            """,
            values,
        )
        row = connection.execute(
            f"SELECT id FROM usage_hourly WHERE "
            + " AND ".join(f"{name} = :{name}" for name in dimensions),
            values,
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _record_latency(connection, hourly_id: int, metric: str, value: Any) -> None:
        bucket_index = latency_bucket_index(value)
        if bucket_index is None:
            return
        connection.execute(
            """
            INSERT INTO usage_latency_histogram(hourly_id, metric, bucket_index, sample_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(hourly_id, metric, bucket_index)
            DO UPDATE SET sample_count = sample_count + 1
            """,
            (hourly_id, metric, bucket_index),
        )

    @staticmethod
    def _where_clause(
            username: str,
            filters: StatsFilters,
            *,
            hourly: bool,
    ) -> Tuple[str, Dict[str, Any]]:
        prefix = "h." if hourly else ""
        clauses = [f"{prefix}username = :username"]
        parameters: Dict[str, Any] = {"username": username}
        time_column = f"{prefix}hour" if hourly else f"{prefix}occurred_at"
        if filters.start_time is not None:
            start = filters.start_time // 3600 * 3600 if hourly else filters.start_time
            clauses.append(f"{time_column} >= :start_time")
            parameters["start_time"] = start
        if filters.end_time is not None:
            end = ((filters.end_time + 3599) // 3600 * 3600) if hourly else filters.end_time
            clauses.append(f"{time_column} < :end_time")
            parameters["end_time"] = end
        source_column = f"{prefix}source"
        if filters.traffic == "external":
            clauses.append(f"{source_column} = 'external_api'")
        elif filters.traffic == "admin":
            clauses.append(
                f"{source_column} IN ('admin_playground', 'credential_test')"
            )
        model_column = f"{prefix}model" if hourly else (
            "COALESCE(NULLIF(upstream_model, ''), requested_model)"
        )
        filter_columns = {
            "model": model_column,
            "api_key_id": f"{prefix}api_key_id",
            "credential_id": f"{prefix}credential_id",
            "outcome": f"{prefix}outcome",
        }
        for name, column in filter_columns.items():
            value = getattr(filters, name)
            if value is not None:
                clauses.append(f"{column} = :{name}")
                parameters[name] = value
        return " AND ".join(clauses), parameters

    @staticmethod
    def _boundary_detail_plan(
            filters: StatsFilters,
            now: int,
            zone: ZoneInfo,
            granularity: str,
    ) -> Tuple[Tuple[Tuple[int, int], ...], set[int], str]:
        """规划可由严格 90 天明细精确替换的范围及本地周期边界小时。"""
        segments: Dict[int, Tuple[int, int]] = {}
        start = filters.start_time
        end = filters.end_time

        def add_segment(hour: int, segment_start: int, segment_end: int) -> None:
            existing = segments.get(hour)
            if existing is None:
                segments[hour] = (segment_start, segment_end)
            else:
                segments[hour] = (
                    min(existing[0], segment_start),
                    max(existing[1], segment_end),
                )

        if start is not None and start % 3600:
            hour = start // 3600 * 3600
            segment_end = min(hour + 3600, end) if end is not None else hour + 3600
            add_segment(hour, start, segment_end)
        if end is not None and end % 3600:
            hour = end // 3600 * 3600
            segment_start = max(hour, start) if start is not None else hour
            add_segment(hour, segment_start, end)

        retention_cutoff = now - DETAIL_RETENTION_DAYS * 86400
        if granularity in ("day", "week"):
            search_start = max(start if start is not None else retention_cutoff, retention_cutoff)
            search_end = min(end if end is not None else now + 1, now + 1)
            if search_start < search_end:
                local_start = datetime.fromtimestamp(search_start, timezone.utc).astimezone(zone)
                boundary_date = local_start.date() + timedelta(days=1)
                while True:
                    local_midnight = datetime.combine(
                        boundary_date,
                        datetime.min.time(),
                        tzinfo=zone,
                    )
                    boundary = int(local_midnight.timestamp())
                    if boundary >= search_end:
                        break
                    if (
                            boundary % 3600
                            and (granularity == "day" or local_midnight.weekday() == 0)
                    ):
                        hour = boundary // 3600 * 3600
                        add_segment(
                            hour,
                            max(hour, start) if start is not None else hour,
                            min(hour + 3600, end) if end is not None else hour + 3600,
                        )
                    boundary_date += timedelta(days=1)

            old_start = start if start is not None else 0
            old_end = min(end if end is not None else retention_cutoff, retention_cutoff)
            has_old_period_boundary = (
                old_end > old_start
                and UsageStatsStore._has_fractional_period_boundary(
                    old_start,
                    old_end,
                    zone,
                    granularity,
                )
            )
        else:
            has_old_period_boundary = False

        exact_segments = []
        exact_hours = set()
        boundary_precision = (
            "hourly_approximate" if has_old_period_boundary else "exact"
        )
        for hour, segment in sorted(segments.items()):
            if segment[0] >= retention_cutoff:
                exact_segments.append(segment)
                exact_hours.add(hour)
            else:
                boundary_precision = "hourly_approximate"
        return tuple(exact_segments), exact_hours, boundary_precision

    @staticmethod
    def _has_fractional_period_boundary(
            start: int,
            end: int,
            zone: ZoneInfo,
            granularity: str,
    ) -> bool:
        """检查范围内每个本地日/周边界，而不是只采样单个 UTC offset。"""
        if start >= end or granularity not in ("day", "week"):
            return False
        local = datetime.fromtimestamp(start, timezone.utc).astimezone(zone)
        if granularity == "day":
            boundary_date = local.date()
            step_days = 1
        else:
            boundary_date = local.date() - timedelta(days=local.weekday())
            step_days = 7
        while True:
            boundary = datetime.combine(
                boundary_date,
                datetime.min.time(),
                tzinfo=zone,
            )
            boundary_timestamp = int(boundary.timestamp())
            if boundary_timestamp <= start:
                boundary_date += timedelta(days=step_days)
                continue
            if boundary_timestamp >= end:
                return False
            if boundary_timestamp % 3600:
                return True
            boundary_date += timedelta(days=step_days)

    @staticmethod
    def _bucket_sql(column: str) -> str:
        clauses = " ".join(
            f"WHEN {column} < {upper_bound} THEN {index}"
            for index, upper_bound in enumerate(LATENCY_BUCKET_UPPER_BOUNDS_MS)
        )
        return f"CASE {clauses} ELSE {len(LATENCY_BUCKET_UPPER_BOUNDS_MS)} END"

    @staticmethod
    def _aggregate_select(alias: str = "") -> str:
        prefix = f"{alias}." if alias else ""
        fields = [
            f"COALESCE(SUM({prefix}request_count), 0) AS request_count",
            f"COALESCE(SUM({prefix}success_count), 0) AS success_count",
            f"COALESCE(SUM({prefix}usage_known_count), 0) AS usage_known_count",
            f"COALESCE(SUM({prefix}credit_sum), 0) AS credit_sum",
            f"COALESCE(SUM({prefix}credit_known_count), 0) AS credit_known_count",
        ]
        for name in (*_OVERVIEW_TOKEN_FIELDS, "total_tokens"):
            fields.extend((
                f"COALESCE(SUM({prefix}{name}_sum), 0) AS {name}_sum",
                f"COALESCE(SUM({prefix}{name}_known_count), 0) AS {name}_known_count",
            ))
        return ", ".join(fields)

    @staticmethod
    def _token_breakdown_from_row(row: Mapping[str, Any]) -> Dict[str, Optional[int]]:
        return {
            name: row[f"{name}_sum"] if row[f"{name}_known_count"] else None
            for name in _OVERVIEW_TOKEN_FIELDS
        }

    @staticmethod
    def _histograms(rows, *, key_column: Optional[str] = None):
        result = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        for row in rows:
            key = row[key_column] if key_column is not None else ""
            result[key][row["metric"]][row["bucket_index"]] += row["sample_count"]
        return result

    @staticmethod
    def _metrics_from_row(
            row: Mapping[str, Any],
            histograms: Mapping[str, Mapping[int, int]],
            *,
            empty_sums_zero: bool = False,
    ) -> Dict[str, Any]:
        request_count = int(row["request_count"] or 0)
        first_output = approximate_percentile(histograms.get("first_output", {}), 0.95)
        total_latency = approximate_percentile(histograms.get("total", {}), 0.95)
        return {
            "request_count": request_count,
            "success_rate": (
                row["success_count"] / request_count if request_count else None
            ),
            "total_tokens": (
                row["total_tokens_sum"]
                if row["total_tokens_known_count"]
                else (0 if empty_sums_zero and not request_count else None)
            ),
            "total_credit": (
                row["credit_sum"]
                if row["credit_known_count"]
                else 0
            ),
            "p95_first_output_ms": first_output.value_ms,
            "p95_first_output_ms_overflow": first_output.overflow,
            "p95_total_ms": total_latency.value_ms,
            "p95_total_ms_overflow": total_latency.overflow,
            "usage_coverage": (
                row["usage_known_count"] / request_count if request_count else None
            ),
        }

    @staticmethod
    def _resolved_granularity(
            filters: StatsFilters,
            minimum_hour: Optional[int],
            maximum_hour: Optional[int],
    ) -> str:
        if filters.granularity != "auto":
            return filters.granularity
        if filters.start_time is not None and filters.end_time is not None:
            span = filters.end_time - filters.start_time
        elif minimum_hour is not None and maximum_hour is not None:
            span = maximum_hour - minimum_hour
        else:
            span = 0
        if span <= 2 * 86400:
            return "hour"
        if span <= 120 * 86400:
            return "day"
        return "week"

    @classmethod
    def _prepare_stats_tables(
            cls,
            connection,
            username: str,
            filters: StatsFilters,
            replaced_hours: set[int],
            detail_segments: Tuple[Tuple[int, int], ...],
            *,
            snapshot_event_id: Optional[int] = None,
    ) -> None:
        connection.execute("DROP TABLE IF EXISTS temp.stats_base")
        connection.execute("DROP TABLE IF EXISTS temp.stats_hist")
        connection.execute(
            """
            CREATE TEMP TABLE stats_base (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_time INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                source TEXT NOT NULL,
                model TEXT NOT NULL,
                api_key_id TEXT NOT NULL,
                api_key_name TEXT NOT NULL,
                credential_id TEXT NOT NULL,
                credential_label TEXT NOT NULL,
                outcome TEXT NOT NULL,
                request_count INTEGER NOT NULL,
                success_count INTEGER NOT NULL,
                usage_known_count INTEGER NOT NULL,
                input_tokens_sum INTEGER NOT NULL,
                input_tokens_known_count INTEGER NOT NULL,
                output_tokens_sum INTEGER NOT NULL,
                output_tokens_known_count INTEGER NOT NULL,
                total_tokens_sum INTEGER NOT NULL,
                total_tokens_known_count INTEGER NOT NULL,
                cache_hit_tokens_sum INTEGER NOT NULL,
                cache_hit_tokens_known_count INTEGER NOT NULL,
                cache_miss_tokens_sum INTEGER NOT NULL,
                cache_miss_tokens_known_count INTEGER NOT NULL,
                credit_sum REAL NOT NULL,
                credit_known_count INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TEMP TABLE stats_hist (
                period_time INTEGER NOT NULL,
                model TEXT NOT NULL,
                api_key_id TEXT NOT NULL,
                credential_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                metric TEXT NOT NULL,
                bucket_index INTEGER NOT NULL,
                sample_count INTEGER NOT NULL
            )
            """
        )
        hourly_where, hourly_parameters = cls._where_clause(
            username, filters, hourly=True
        )
        excluded = ""
        if replaced_hours:
            placeholders = []
            for index, hour in enumerate(sorted(replaced_hours)):
                key = f"replaced_hour_{index}"
                hourly_parameters[key] = hour
                placeholders.append(f":{key}")
            excluded = f" AND h.hour NOT IN ({', '.join(placeholders)})"
        connection.execute(
            """
            INSERT INTO stats_base (
                period_time, hour, source, model, api_key_id, api_key_name,
                credential_id, credential_label, outcome, request_count,
                success_count, usage_known_count, input_tokens_sum,
                input_tokens_known_count, output_tokens_sum,
                output_tokens_known_count, total_tokens_sum,
                total_tokens_known_count, cache_hit_tokens_sum,
                cache_hit_tokens_known_count, cache_miss_tokens_sum,
                cache_miss_tokens_known_count, credit_sum, credit_known_count
            )
            SELECT
                h.hour, h.hour, h.source, h.model, h.api_key_id, h.api_key_name,
                h.credential_id, h.credential_label, h.outcome, h.request_count,
                h.success_count, h.usage_known_count, h.input_tokens_sum,
                h.input_tokens_known_count, h.output_tokens_sum,
                h.output_tokens_known_count, h.total_tokens_sum,
                h.total_tokens_known_count, h.cache_hit_tokens_sum,
                h.cache_hit_tokens_known_count, h.cache_miss_tokens_sum,
                h.cache_miss_tokens_known_count, h.credit_sum, h.credit_known_count
            FROM usage_hourly h
            WHERE """ + hourly_where + excluded,
            hourly_parameters,
        )
        connection.execute(
            """
            INSERT INTO stats_hist (
                period_time, model, api_key_id, credential_id, outcome,
                metric, bucket_index, sample_count
            )
            SELECT
                h.hour, h.model, h.api_key_id, h.credential_id, h.outcome,
                l.metric, l.bucket_index, l.sample_count
            FROM usage_latency_histogram l
            JOIN usage_hourly h ON h.id = l.hourly_id
            WHERE """ + hourly_where + excluded,
            hourly_parameters,
        )
        if snapshot_event_id is not None:
            cls._subtract_post_snapshot_events(
                connection,
                username,
                filters,
                replaced_hours,
                snapshot_event_id,
            )
        if not detail_segments:
            return

        detail_where, detail_parameters = cls._where_clause(
            username, filters, hourly=False
        )
        segment_clauses = []
        for index, (start, end) in enumerate(detail_segments):
            start_key = f"segment_start_{index}"
            end_key = f"segment_end_{index}"
            detail_parameters[start_key] = start
            detail_parameters[end_key] = end
            segment_clauses.append(
                f"(occurred_at >= :{start_key} AND occurred_at < :{end_key})"
            )
        detail_where += " AND (" + " OR ".join(segment_clauses) + ")"
        if snapshot_event_id is not None:
            detail_where += " AND id <= :snapshot_event_id"
            detail_parameters["snapshot_event_id"] = snapshot_event_id
        model_expression = "COALESCE(NULLIF(upstream_model, ''), requested_model)"
        connection.execute(
            """
            INSERT INTO stats_base (
                period_time, hour, source, model, api_key_id, api_key_name,
                credential_id, credential_label, outcome, request_count,
                success_count, usage_known_count, input_tokens_sum,
                input_tokens_known_count, output_tokens_sum,
                output_tokens_known_count, total_tokens_sum,
                total_tokens_known_count, cache_hit_tokens_sum,
                cache_hit_tokens_known_count, cache_miss_tokens_sum,
                cache_miss_tokens_known_count, credit_sum, credit_known_count
            )
            SELECT
                occurred_at, CAST(occurred_at / 3600 AS INTEGER) * 3600,
                source, """ + model_expression + """,
                COALESCE(api_key_id, ''), COALESCE(api_key_name, ''),
                COALESCE(credential_id, ''), COALESCE(credential_label, ''),
                outcome, 1, CASE WHEN outcome = 'success' THEN 1 ELSE 0 END,
                CASE WHEN total_tokens IS NOT NULL THEN 1 ELSE 0 END,
                COALESCE(input_tokens, 0),
                CASE WHEN input_tokens IS NOT NULL THEN 1 ELSE 0 END,
                COALESCE(output_tokens, 0),
                CASE WHEN output_tokens IS NOT NULL THEN 1 ELSE 0 END,
                COALESCE(total_tokens, 0),
                CASE WHEN total_tokens IS NOT NULL THEN 1 ELSE 0 END,
                COALESCE(cache_hit_tokens, 0),
                CASE WHEN cache_hit_tokens IS NOT NULL THEN 1 ELSE 0 END,
                COALESCE(cache_miss_tokens, 0),
                CASE WHEN cache_miss_tokens IS NOT NULL THEN 1 ELSE 0 END,
                COALESCE(credit, 0),
                CASE WHEN credit IS NOT NULL THEN 1 ELSE 0 END
            FROM usage_events
            WHERE """ + detail_where,
            detail_parameters,
        )
        for metric, column in (
            ("total", "duration_ms"),
            ("first_output", "first_output_ms"),
        ):
            connection.execute(
                """
                INSERT INTO stats_hist (
                    period_time, model, api_key_id, credential_id, outcome,
                    metric, bucket_index, sample_count
                )
                SELECT
                    occurred_at, """ + model_expression + """,
                    COALESCE(api_key_id, ''), COALESCE(credential_id, ''),
                    outcome, :histogram_metric, """ + cls._bucket_sql(column) + """, 1
                FROM usage_events
                WHERE """ + detail_where + f" AND outcome = 'success' AND {column} IS NOT NULL",
                {**detail_parameters, "histogram_metric": metric},
            )

    @classmethod
    def _subtract_post_snapshot_events(
            cls,
            connection,
            username: str,
            filters: StatsFilters,
            replaced_hours: set[int],
            snapshot_event_id: int,
    ) -> None:
        """从当前小时汇总中扣除首屏快照之后才写入的明细事件。"""
        rounded_filters = replace(
            filters,
            start_time=(
                filters.start_time // 3600 * 3600
                if filters.start_time is not None
                else None
            ),
            end_time=(
                (filters.end_time + 3599) // 3600 * 3600
                if filters.end_time is not None
                else None
            ),
        )
        detail_where, detail_parameters = cls._where_clause(
            username,
            rounded_filters,
            hourly=False,
        )
        detail_where += " AND id > :snapshot_event_id"
        detail_parameters["snapshot_event_id"] = snapshot_event_id
        if replaced_hours:
            placeholders = []
            for index, hour in enumerate(sorted(replaced_hours)):
                key = f"snapshot_replaced_hour_{index}"
                detail_parameters[key] = hour
                placeholders.append(f":{key}")
            detail_where += (
                " AND CAST(occurred_at / 3600 AS INTEGER) * 3600 "
                f"NOT IN ({', '.join(placeholders)})"
            )

        model_expression = "COALESCE(NULLIF(upstream_model, ''), requested_model)"
        connection.execute(
            """
            INSERT INTO stats_base (
                period_time, hour, source, model, api_key_id, api_key_name,
                credential_id, credential_label, outcome, request_count,
                success_count, usage_known_count, input_tokens_sum,
                input_tokens_known_count, output_tokens_sum,
                output_tokens_known_count, total_tokens_sum,
                total_tokens_known_count, cache_hit_tokens_sum,
                cache_hit_tokens_known_count, cache_miss_tokens_sum,
                cache_miss_tokens_known_count, credit_sum, credit_known_count
            )
            SELECT
                CAST(occurred_at / 3600 AS INTEGER) * 3600,
                CAST(occurred_at / 3600 AS INTEGER) * 3600,
                source, """ + model_expression + """,
                COALESCE(api_key_id, ''), COALESCE(api_key_name, ''),
                COALESCE(credential_id, ''), COALESCE(credential_label, ''),
                outcome, -1,
                CASE WHEN outcome = 'success' THEN -1 ELSE 0 END,
                CASE WHEN total_tokens IS NOT NULL THEN -1 ELSE 0 END,
                -COALESCE(input_tokens, 0),
                CASE WHEN input_tokens IS NOT NULL THEN -1 ELSE 0 END,
                -COALESCE(output_tokens, 0),
                CASE WHEN output_tokens IS NOT NULL THEN -1 ELSE 0 END,
                -COALESCE(total_tokens, 0),
                CASE WHEN total_tokens IS NOT NULL THEN -1 ELSE 0 END,
                -COALESCE(cache_hit_tokens, 0),
                CASE WHEN cache_hit_tokens IS NOT NULL THEN -1 ELSE 0 END,
                -COALESCE(cache_miss_tokens, 0),
                CASE WHEN cache_miss_tokens IS NOT NULL THEN -1 ELSE 0 END,
                -COALESCE(credit, 0),
                CASE WHEN credit IS NOT NULL THEN -1 ELSE 0 END
            FROM usage_events
            WHERE """ + detail_where,
            detail_parameters,
        )
        for metric, column in (
            ("total", "duration_ms"),
            ("first_output", "first_output_ms"),
        ):
            connection.execute(
                """
                INSERT INTO stats_hist (
                    period_time, model, api_key_id, credential_id, outcome,
                    metric, bucket_index, sample_count
                )
                SELECT
                    occurred_at, """ + model_expression + """,
                    COALESCE(api_key_id, ''), COALESCE(credential_id, ''),
                    outcome, :histogram_metric, """ + cls._bucket_sql(column) + """, -1
                FROM usage_events
                WHERE """ + detail_where + f" AND outcome = 'success' AND {column} IS NOT NULL",
                {**detail_parameters, "histogram_metric": metric},
            )

    @staticmethod
    def _period_start(timestamp: int, zone: ZoneInfo, granularity: str) -> int:
        if granularity == "hour":
            return timestamp // 3600 * 3600
        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(zone)
        period_date = local.date()
        if granularity == "week":
            period_date -= timedelta(days=local.weekday())
        return int(datetime.combine(
            period_date,
            datetime.min.time(),
            tzinfo=zone,
        ).timestamp())

    @staticmethod
    def _next_period(timestamp: int, zone: ZoneInfo, granularity: str) -> int:
        if granularity == "hour":
            return timestamp + 3600
        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(zone)
        step = 1 if granularity == "day" else 7
        return int(datetime.combine(
            local.date() + timedelta(days=step),
            datetime.min.time(),
            tzinfo=zone,
        ).timestamp())

    @staticmethod
    def _period_label(timestamp: int, zone: ZoneInfo, granularity: str) -> str:
        local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(zone)
        return (
            local.isoformat(timespec="seconds")
            if granularity == "hour"
            else local.date().isoformat()
        )

    @classmethod
    def _series_periods(
            cls,
            filters: StatsFilters,
            zone: ZoneInfo,
            granularity: str,
            minimum_time: Optional[int],
            maximum_time: Optional[int],
    ) -> list[Tuple[int, int, str]]:
        if minimum_time is None or maximum_time is None:
            return []
        start = filters.start_time
        if start is None or start == 0:
            start = minimum_time
        end = filters.end_time if filters.end_time is not None else maximum_time + 1
        current = cls._period_start(start, zone, granularity)
        periods = []
        while current < end:
            next_period = cls._next_period(current, zone, granularity)
            periods.append((current, next_period, cls._period_label(current, zone, granularity)))
            if len(periods) > MAX_SERIES_POINTS:
                raise ValueError(
                    f"series contains more than {MAX_SERIES_POINTS} periods; use a coarser granularity"
                )
            current = next_period
        return periods

    @classmethod
    def _group_rows(
            cls,
            connection,
            group_column: str,
            label_column: Optional[str],
            limit: Optional[int],
    ):
        label_select = f", base.{group_column} AS label"
        if label_column is not None:
            label_select = (
                f", (SELECT latest.{label_column} FROM stats_base latest "
                f"WHERE latest.{group_column} = base.{group_column} "
                "ORDER BY latest.period_time DESC, latest.row_id DESC LIMIT 1) AS label"
            )
        limit_clause = "" if limit is None else " LIMIT :group_limit"
        parameters = {} if limit is None else {"group_limit": limit}
        return connection.execute(
            f"SELECT base.{group_column} AS identifier{label_select}, "
            f"{cls._aggregate_select('base')} FROM stats_base base "
            f"WHERE base.{group_column} <> '' GROUP BY base.{group_column} "
            "ORDER BY request_count DESC, identifier ASC"
            + limit_clause,
            parameters,
        ).fetchall()

    @staticmethod
    def _facet_conditions(
            filters: StatsFilters,
            excluded_filter: str,
            alias: str,
    ) -> Tuple[list[str], Dict[str, Any]]:
        columns = {
            "model": "model",
            "api_key_id": "api_key_id",
            "credential_id": "credential_id",
            "outcome": "outcome",
        }
        clauses = []
        parameters = {}
        for name, column in columns.items():
            value = getattr(filters, name)
            if name != excluded_filter and value is not None:
                clauses.append(f"{alias}.{column} = :{name}")
                parameters[name] = value
        return clauses, parameters

    @classmethod
    def _facet_group_rows(
            cls,
            connection,
            group_column: str,
            label_column: Optional[str],
            filters: StatsFilters,
            excluded_filter: str,
    ):
        clauses, parameters = cls._facet_conditions(
            filters,
            excluded_filter,
            "base",
        )
        where_clause = f"base.{group_column} <> ''"
        if clauses:
            where_clause += " AND " + " AND ".join(clauses)
        label_select = f", base.{group_column} AS label"
        if label_column is not None:
            latest_clauses, _ = cls._facet_conditions(
                filters,
                excluded_filter,
                "latest",
            )
            latest_where = f"latest.{group_column} = base.{group_column}"
            if latest_clauses:
                latest_where += " AND " + " AND ".join(latest_clauses)
            label_select = (
                f", (SELECT latest.{label_column} FROM stats_base latest "
                f"WHERE {latest_where} "
                "ORDER BY latest.period_time DESC, latest.row_id DESC LIMIT 1) AS label"
            )
        return connection.execute(
            f"SELECT base.{group_column} AS identifier{label_select}, "
            "SUM(base.request_count) AS request_count FROM stats_base base "
            f"WHERE {where_clause} GROUP BY base.{group_column} "
            "ORDER BY request_count DESC, identifier ASC",
            parameters,
        ).fetchall()

    @classmethod
    def _faceted_dimensions(
            cls,
            connection,
            filters: StatsFilters,
    ) -> Dict[str, Any]:
        model_rows = cls._facet_group_rows(
            connection,
            "model",
            None,
            filters,
            "model",
        )
        api_key_rows = cls._facet_group_rows(
            connection,
            "api_key_id",
            "api_key_name",
            filters,
            "api_key_id",
        )
        credential_rows = cls._facet_group_rows(
            connection,
            "credential_id",
            "credential_label",
            filters,
            "credential_id",
        )
        outcome_rows = cls._facet_group_rows(
            connection,
            "outcome",
            None,
            filters,
            "outcome",
        )
        return {
            "models": [row["identifier"] for row in model_rows],
            "api_keys": [
                {"id": row["identifier"], "name": row["label"]}
                for row in api_key_rows
            ],
            "credentials": [
                {"id": row["identifier"], "label": row["label"]}
                for row in credential_rows
            ],
            "outcomes": sorted(row["identifier"] for row in outcome_rows),
        }

    @classmethod
    def _breakdown_rows(
            cls,
            connection,
            group_column: str,
            output_key: str,
            label_key: Optional[str],
            label_column: Optional[str],
    ) -> list[Dict[str, Any]]:
        rows = cls._group_rows(
            connection,
            group_column,
            label_column,
            BREAKDOWN_LIMIT,
        )
        identifiers = [row["identifier"] for row in rows]
        histograms = {}
        if identifiers:
            placeholders = ", ".join("?" for _ in identifiers)
            histogram_rows = connection.execute(
                f"SELECT {group_column} AS identifier, metric, bucket_index, "
                "SUM(sample_count) AS sample_count FROM stats_hist "
                f"WHERE {group_column} IN ({placeholders}) "
                f"GROUP BY {group_column}, metric, bucket_index",
                identifiers,
            ).fetchall()
            histograms = cls._histograms(histogram_rows, key_column="identifier")
        result = []
        for row in rows:
            identifier = row["identifier"]
            item = {output_key: identifier, **cls._metrics_from_row(
                row, histograms.get(identifier, {})
            )}
            if label_key is not None:
                item[label_key] = row["label"]
            result.append(item)
        return result

    @classmethod
    def _read_overview(
            cls,
            connection,
            filters: StatsFilters,
            zone: ZoneInfo,
            granularity: str,
    ) -> Tuple[Dict[str, Any], list, Dict[str, Any], Dict[str, Any]]:
        total_row = connection.execute(
            f"SELECT {cls._aggregate_select()} FROM stats_base"
        ).fetchone()
        total_histogram_rows = connection.execute(
            "SELECT metric, bucket_index, SUM(sample_count) AS sample_count "
            "FROM stats_hist GROUP BY metric, bucket_index"
        ).fetchall()
        total_histograms = cls._histograms(total_histogram_rows).get("", {})
        totals = {
            **cls._metrics_from_row(total_row, total_histograms),
            **cls._token_breakdown_from_row(total_row),
        }

        extent = connection.execute(
            "SELECT MIN(period_time) AS minimum_time, MAX(period_time) AS maximum_time "
            "FROM stats_base"
        ).fetchone()
        periods = cls._series_periods(
            filters,
            zone,
            granularity,
            extent["minimum_time"],
            extent["maximum_time"],
        )
        series = []
        if periods:
            connection.execute("DROP TABLE IF EXISTS temp.stats_periods")
            connection.execute(
                "CREATE TEMP TABLE stats_periods ("
                "period_start INTEGER PRIMARY KEY, period_end INTEGER NOT NULL, period TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO stats_periods(period_start, period_end, period) VALUES (?, ?, ?)",
                periods,
            )
            aggregate_rows = connection.execute(
                f"SELECT p.period_start, p.period, {cls._aggregate_select('b')} "
                "FROM stats_periods p LEFT JOIN stats_base b "
                "ON b.period_time >= p.period_start AND b.period_time < p.period_end "
                "GROUP BY p.period_start, p.period ORDER BY p.period_start"
            ).fetchall()
            series_histogram_rows = connection.execute(
                "SELECT p.period_start, h.metric, h.bucket_index, "
                "SUM(h.sample_count) AS sample_count FROM stats_periods p "
                "JOIN stats_hist h ON h.period_time >= p.period_start "
                "AND h.period_time < p.period_end "
                "GROUP BY p.period_start, h.metric, h.bucket_index"
            ).fetchall()
            series_histograms = cls._histograms(
                series_histogram_rows,
                key_column="period_start",
            )
            series = [
                {
                    "period_start": row["period_start"],
                    "period": row["period"],
                    **cls._metrics_from_row(
                        row,
                        series_histograms.get(row["period_start"], {}),
                        empty_sums_zero=True,
                    ),
                }
                for row in aggregate_rows
            ]

        model_dimensions = cls._group_rows(
            connection, "model", None, None
        )
        api_key_dimensions = cls._group_rows(
            connection, "api_key_id", "api_key_name", None
        )
        credential_dimensions = cls._group_rows(
            connection, "credential_id", "credential_label", None
        )
        dimensions = {
            "models": [row["identifier"] for row in model_dimensions],
            "api_keys": [
                {"id": row["identifier"], "name": row["label"]}
                for row in api_key_dimensions
            ],
            "credentials": [
                {"id": row["identifier"], "label": row["label"]}
                for row in credential_dimensions
            ],
            "outcomes": [
                row["outcome"]
                for row in connection.execute(
                    "SELECT outcome FROM stats_base GROUP BY outcome ORDER BY outcome"
                ).fetchall()
            ],
        }
        breakdowns = {
            "models": cls._breakdown_rows(
                connection, "model", "model", None, None
            ),
            "api_keys": cls._breakdown_rows(
                connection, "api_key_id", "id", "name", "api_key_name"
            ),
            "credentials": cls._breakdown_rows(
                connection,
                "credential_id",
                "id",
                "label",
                "credential_label",
            ),
        }
        return totals, series, dimensions, breakdowns

    def get_overview(
            self,
            username: str,
            filters: Union[StatsFilters, Mapping[str, Any], None] = None,
            *,
            dropped_events: Optional[int] = None,
    ) -> Dict[str, Any]:
        """返回由 SQL 有界聚合生成的总览、趋势、维度和排行。"""
        username = _validate_username(username)
        normalized_filters, zone = _coerce_filters(filters)
        query_now = int(time.time())
        database = self._database()
        with database.connect(create=False) as connection:
            connection.execute("BEGIN")
            hourly_where, hourly_parameters = self._where_clause(
                username, normalized_filters, hourly=True
            )
            extent = connection.execute(
                f"SELECT MIN(h.hour) AS minimum_hour, MAX(h.hour) AS maximum_hour "
                f"FROM usage_hourly h WHERE {hourly_where}",
                hourly_parameters,
            ).fetchone()
            granularity = self._resolved_granularity(
                normalized_filters,
                extent["minimum_hour"],
                extent["maximum_hour"],
            )
            detail_segments, replaced_hours, boundary_precision = (
                self._boundary_detail_plan(
                    normalized_filters,
                    query_now,
                    zone,
                    granularity,
                )
            )
            self._prepare_stats_tables(
                connection,
                username,
                normalized_filters,
                replaced_hours,
                detail_segments,
            )
            totals, series, dimensions, breakdowns = self._read_overview(
                connection,
                normalized_filters,
                zone,
                granularity,
            )
            if any((
                    normalized_filters.model,
                    normalized_filters.api_key_id,
                    normalized_filters.credential_id,
                    normalized_filters.outcome,
            )):
                facet_base_filters = replace(
                    normalized_filters,
                    model=None,
                    api_key_id=None,
                    credential_id=None,
                    outcome=None,
                )
                self._prepare_stats_tables(
                    connection,
                    username,
                    facet_base_filters,
                    replaced_hours,
                    detail_segments,
                )
                dimensions = self._faceted_dimensions(
                    connection,
                    normalized_filters,
                )

        reported_drops = (
            self.get_dropped_events(username)
            if dropped_events is None
            else int(dropped_events)
        )
        if reported_drops < 0:
            raise ValueError("dropped_events must not be negative")
        return {
            "totals": totals,
            "series": series,
            "dimensions": dimensions,
            "breakdowns": breakdowns,
            "data_quality": {
                "usage_coverage": totals["usage_coverage"],
                "dropped_events": reported_drops,
                "detail_retention_days": DETAIL_RETENTION_DAYS,
                "boundary_precision": boundary_precision,
            },
        }

    @staticmethod
    def _dimension_cursor_signature(
            username: str,
            dimension: str,
            filters: StatsFilters,
            search: str,
            snapshot_event_id: int,
            snapshot_time: int,
    ) -> str:
        payload = json.dumps(
            {
                "username": username,
                "dimension": dimension,
                "filters": asdict(filters),
                "search": search,
                "snapshot_event_id": snapshot_event_id,
                "snapshot_time": snapshot_time,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:24]

    @staticmethod
    def _encode_dimension_cursor(
            request_count: int,
            identifier: str,
            signature: str,
            snapshot_event_id: int,
            snapshot_time: int,
    ) -> str:
        payload = json.dumps(
            {
                "count": request_count,
                "id": identifier,
                "signature": signature,
                "snapshot_event_id": snapshot_event_id,
                "snapshot_time": snapshot_time,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode_dimension_cursor(cursor: str) -> Tuple[int, str, int, int, str]:
        try:
            encoded = cursor.encode("ascii")
            payload = json.loads(base64.urlsafe_b64decode(
                encoded + b"=" * (-len(encoded) % 4)
            ))
            count = payload["count"]
            identifier = payload["id"]
            cursor_signature = payload["signature"]
            snapshot_event_id = payload["snapshot_event_id"]
            snapshot_time = payload["snapshot_time"]
        except (UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid dimension cursor") from error
        if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or count > SQLITE_MAX_INTEGER
                or not isinstance(identifier, str)
                or not identifier
                or isinstance(snapshot_event_id, bool)
                or not isinstance(snapshot_event_id, int)
                or not 0 <= snapshot_event_id <= SQLITE_MAX_INTEGER
                or isinstance(snapshot_time, bool)
                or not isinstance(snapshot_time, int)
                or not 0 <= snapshot_time <= MAX_STATS_TIMESTAMP
                or not isinstance(cursor_signature, str)
        ):
            raise ValueError("dimension cursor does not match the current query")
        return count, identifier, snapshot_event_id, snapshot_time, cursor_signature

    def list_dimension_values(
            self,
            username: str,
            dimension: str,
            filters: Union[StatsFilters, Mapping[str, Any], None] = None,
            *,
            search: str = "",
            cursor: Optional[str] = None,
            limit: int = 50,
    ) -> Dict[str, Any]:
        """按请求数稳定分页返回完整历史维度及其聚合指标。"""
        dimensions = {
            "models": ("model", None),
            "api_keys": ("api_key_id", "api_key_name"),
            "credentials": ("credential_id", "credential_label"),
        }
        if dimension not in dimensions:
            raise ValueError(f"Unsupported statistics dimension: {dimension}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        normalized_search = str(search or "").strip()
        if len(normalized_search) > 100:
            raise ValueError("search must not exceed 100 characters")
        username = _validate_username(username)
        normalized_filters, zone = _coerce_filters(filters)
        decoded_cursor = (
            self._decode_dimension_cursor(cursor) if cursor is not None else None
        )
        cursor_values = None
        snapshot_event_id = 0
        snapshot_time = 0
        cursor_signature = None
        if decoded_cursor is not None:
            (
                cursor_count,
                cursor_identifier,
                snapshot_event_id,
                snapshot_time,
                cursor_signature,
            ) = decoded_cursor
            cursor_values = (cursor_count, cursor_identifier)
            expected_signature = self._dimension_cursor_signature(
                username,
                dimension,
                normalized_filters,
                normalized_search,
                snapshot_event_id,
                snapshot_time,
            )
            if cursor_signature != expected_signature:
                raise ValueError("dimension cursor does not match the current query")
        group_column, label_column = dimensions[dimension]
        database = self._database()
        with database.connect(create=False) as connection:
            connection.execute("BEGIN")
            sequence_row = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'usage_events'"
            ).fetchone()
            current_event_id = int(sequence_row[0]) if sequence_row is not None else 0
            if decoded_cursor is None:
                snapshot_event_id = current_event_id
                snapshot_time = int(time.time())
                cursor_signature = self._dimension_cursor_signature(
                    username,
                    dimension,
                    normalized_filters,
                    normalized_search,
                    snapshot_event_id,
                    snapshot_time,
                )
            else:
                cleanup_row = connection.execute(
                    "SELECT detail_cutoff FROM usage_retention_state WHERE id = 1"
                ).fetchone()
                snapshot_cutoff = snapshot_time - DETAIL_RETENTION_DAYS * 86400
                if (
                        snapshot_event_id > current_event_id
                        or (
                            cleanup_row is not None
                            and int(cleanup_row[0]) > snapshot_cutoff
                        )
                ):
                    raise ValueError(
                        "dimension cursor snapshot is no longer valid; "
                        "fetch page 1 again"
                    )
            detail_segments, replaced_hours, _precision = self._boundary_detail_plan(
                normalized_filters,
                snapshot_time,
                zone,
                "hour",
            )
            self._prepare_stats_tables(
                connection,
                username,
                normalized_filters,
                replaced_hours,
                detail_segments,
                snapshot_event_id=snapshot_event_id,
            )
            parameters: Dict[str, Any] = {"page_limit": limit + 1}
            where_clauses = [f"base.{group_column} <> ''"]
            if normalized_search:
                escaped_search = (
                    normalized_search.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                    .lower()
                )
                parameters["search"] = f"%{escaped_search}%"
                search_columns = [
                    f"LOWER(candidate.{group_column}) LIKE :search ESCAPE '\\'"
                ]
                if label_column is not None:
                    search_columns.append(
                        f"LOWER(candidate.{label_column}) LIKE :search ESCAPE '\\'"
                    )
                where_clauses.append(
                    f"base.{group_column} IN ("
                    f"SELECT candidate.{group_column} FROM stats_base candidate "
                    f"WHERE candidate.{group_column} <> '' AND ("
                    + " OR ".join(search_columns)
                    + f") GROUP BY candidate.{group_column} "
                    "HAVING SUM(candidate.request_count) > 0)"
                )
            having_clauses = ["SUM(base.request_count) > 0"]
            if cursor_values is not None:
                parameters["cursor_count"], parameters["cursor_id"] = cursor_values
                having_clauses.append(
                    "(SUM(base.request_count) < :cursor_count OR "
                    "(SUM(base.request_count) = :cursor_count "
                    f"AND base.{group_column} > :cursor_id))"
                )
            having = "HAVING " + " AND ".join(having_clauses)
            label_select = f"base.{group_column} AS label"
            if label_column is not None:
                label_select = (
                    f"(SELECT latest.{label_column} FROM stats_base latest "
                    f"WHERE latest.{group_column} = base.{group_column} "
                    f"GROUP BY latest.{label_column}, latest.period_time "
                    "HAVING SUM(latest.request_count) > 0 "
                    "ORDER BY latest.period_time DESC, MAX(latest.row_id) DESC "
                    "LIMIT 1) AS label"
                )
            rows = connection.execute(
                f"SELECT base.{group_column} AS identifier, {label_select}, "
                f"{self._aggregate_select('base')} FROM stats_base base "
                "WHERE " + " AND ".join(where_clauses) + " "
                f"GROUP BY base.{group_column} {having} "
                "ORDER BY request_count DESC, identifier ASC LIMIT :page_limit",
                parameters,
            ).fetchall()
            has_more = len(rows) > limit
            selected = rows[:limit]
            identifiers = [row["identifier"] for row in selected]
            histograms = {}
            if identifiers:
                placeholders = ", ".join("?" for _ in identifiers)
                histogram_rows = connection.execute(
                    f"SELECT {group_column} AS identifier, metric, bucket_index, "
                    "SUM(sample_count) AS sample_count FROM stats_hist "
                    f"WHERE {group_column} IN ({placeholders}) "
                    f"GROUP BY {group_column}, metric, bucket_index",
                    identifiers,
                ).fetchall()
                histograms = self._histograms(
                    histogram_rows,
                    key_column="identifier",
                )
            items = [
                {
                    "id": row["identifier"],
                    "label": row["label"],
                    **self._metrics_from_row(
                        row,
                        histograms.get(row["identifier"], {}),
                    ),
                }
                for row in selected
            ]
        next_cursor = None
        if has_more and selected:
            last = selected[-1]
            next_cursor = self._encode_dimension_cursor(
                int(last["request_count"]),
                last["identifier"],
                cursor_signature,
                snapshot_event_id,
                snapshot_time,
            )
        return {"items": items, "next_cursor": next_cursor}

    def list_events(
            self,
            username: str,
            filters: Union[StatsFilters, Mapping[str, Any], None] = None,
            *,
            page: int = 1,
            page_size: int = 20,
            snapshot_id: Optional[int] = None,
            snapshot_time: Optional[int] = None,
    ) -> Dict[str, Any]:
        """基于固定事件与保留时间快照分页返回最近 90 天的脱敏明细。"""
        username = _validate_username(username)
        normalized_filters, _zone = _coerce_filters(filters)
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("page must be a positive integer")
        if (
                isinstance(page_size, bool)
                or not isinstance(page_size, int)
                or not 1 <= page_size <= 100
        ):
            raise ValueError("page_size must be between 1 and 100")
        offset = (page - 1) * page_size
        if offset > SQLITE_MAX_INTEGER:
            raise ValueError("page offset is too large")
        normalized_snapshot = self._normalize_request_snapshot(
            snapshot_id,
            snapshot_time,
        )
        if normalized_snapshot is not None:
            snapshot_id, snapshot_time = normalized_snapshot
        database = self._database()
        where, parameters = self._where_clause(
            username, normalized_filters, hourly=False
        )
        with database.connect(create=False) as connection:
            connection.execute("BEGIN")
            sequence_row = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name = 'usage_events'"
            ).fetchone()
            current_event_id = int(sequence_row[0]) if sequence_row is not None else 0
            if snapshot_id is None:
                snapshot_time = int(time.time())
                snapshot_id = current_event_id
            else:
                self._validate_request_snapshot_availability(
                    connection,
                    snapshot_id,
                    snapshot_time,
                    current_event_id,
                )
            where += " AND id <= :snapshot_id AND occurred_at >= :retention_cutoff"
            parameters["snapshot_id"] = snapshot_id
            parameters["retention_cutoff"] = snapshot_time - DETAIL_RETENTION_DAYS * 86400
            parameters["page_size"] = page_size
            parameters["offset"] = offset
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM usage_events WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM usage_events WHERE {where} "
                "ORDER BY id DESC LIMIT :page_size OFFSET :offset",
                parameters,
            ).fetchall()
        return {
            "items": [self._event_dict(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot_time,
        }

    @staticmethod
    def _normalize_request_snapshot(
            snapshot_id: Optional[int],
            snapshot_time: Optional[int],
    ) -> Optional[Tuple[int, int]]:
        if snapshot_id is None and snapshot_time is None:
            return None
        if snapshot_id is None or snapshot_time is None:
            raise ValueError("invalid request pagination snapshot")
        normalized_id = _nonnegative_int(snapshot_id)
        normalized_time = _nonnegative_int(snapshot_time)
        if (
                normalized_id is None
                or normalized_id > SQLITE_MAX_INTEGER
                or normalized_time is None
                or normalized_time > MAX_STATS_TIMESTAMP
        ):
            raise ValueError("invalid request pagination snapshot")
        return normalized_id, normalized_time

    @staticmethod
    def _validate_request_snapshot_availability(
            connection,
            snapshot_id: int,
            snapshot_time: int,
            current_event_id: int,
    ) -> None:
        cleanup_row = connection.execute(
            "SELECT detail_cutoff FROM usage_retention_state WHERE id = 1"
        ).fetchone()
        snapshot_cutoff = snapshot_time - DETAIL_RETENTION_DAYS * 86400
        if (
                snapshot_id > current_event_id
                or (
                    cleanup_row is not None
                    and int(cleanup_row[0]) > snapshot_cutoff
                )
        ):
            raise ValueError(
                "request pagination snapshot is no longer valid; fetch page 1 again"
            )

    def get_event(
            self,
            username: str,
            event_id: int,
            *,
            snapshot_id: Optional[int] = None,
            snapshot_time: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """按用户名读取单条明细，用户不匹配与不存在均返回 ``None``。"""
        username = _validate_username(username)
        normalized_id = _nonnegative_int(event_id)
        if normalized_id is None or normalized_id > SQLITE_MAX_INTEGER:
            raise ValueError("event_id must be a non-negative integer")
        normalized_snapshot = self._normalize_request_snapshot(
            snapshot_id,
            snapshot_time,
        )
        database = self._database()
        with database.connect(create=False) as connection:
            connection.execute("BEGIN")
            if normalized_snapshot is None:
                retention_cutoff = int(time.time()) - DETAIL_RETENTION_DAYS * 86400
                snapshot_clause = ""
                parameters = (username, normalized_id, retention_cutoff)
            else:
                normalized_snapshot_id, normalized_snapshot_time = normalized_snapshot
                sequence_row = connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'usage_events'"
                ).fetchone()
                current_event_id = int(sequence_row[0]) if sequence_row is not None else 0
                self._validate_request_snapshot_availability(
                    connection,
                    normalized_snapshot_id,
                    normalized_snapshot_time,
                    current_event_id,
                )
                retention_cutoff = (
                    normalized_snapshot_time - DETAIL_RETENTION_DAYS * 86400
                )
                snapshot_clause = " AND id <= ?"
                parameters = (
                    username,
                    normalized_id,
                    retention_cutoff,
                    normalized_snapshot_id,
                )
            row = connection.execute(
                "SELECT * FROM usage_events "
                "WHERE username = ? AND id = ? AND occurred_at >= ?"
                + snapshot_clause,
                parameters,
            ).fetchone()
        return None if row is None else self._event_dict(row)

    @staticmethod
    def _event_dict(row) -> Dict[str, Any]:
        item = dict(row)
        item["started_at"] = item.pop("occurred_at")
        item.pop("username")
        if item["client_stream"] is not None:
            item["client_stream"] = bool(item["client_stream"])
        return item

    def cleanup_old_events(self, now: Optional[int] = None) -> int:
        """删除超过 90 天的逐请求明细，永久小时汇总不受影响。"""
        normalized_now = _nonnegative_int(int(time.time()) if now is None else now)
        if normalized_now is None:
            raise ValueError("now must be a non-negative integer or None")
        database = self._database()
        deleted_total = 0
        while True:
            with database.connect(create=False) as connection:
                deleted = self._cleanup_in_connection(connection, normalized_now)
            deleted_total += deleted
            if deleted < CLEANUP_BATCH_SIZE:
                return deleted_total


class UsageStatsRetentionManager:
    """在启动及运行期独立执行逐请求明细保留期清理。"""

    def __init__(
            self,
            store: UsageStatsStore,
            *,
            interval_seconds: float = 3600,
            retry_seconds: float = 300,
    ) -> None:
        self._store = store
        self._interval_seconds = interval_seconds
        self._retry_seconds = retry_seconds
        self._stop_event: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None

    async def startup(self) -> None:
        if self._task is not None:
            raise RuntimeError("usage statistics retention manager is already running")
        await asyncio.to_thread(self._store.cleanup_old_events)
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
        delay = self._interval_seconds
        while self._stop_event is not None:
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
            try:
                await asyncio.to_thread(self._store.cleanup_old_events)
            except Exception:
                logger.exception("Failed to clean up expired usage statistics details")
                delay = self._retry_seconds
            else:
                delay = self._interval_seconds


usage_stats_store = UsageStatsStore()
usage_stats_retention_manager = UsageStatsRetentionManager(usage_stats_store)
