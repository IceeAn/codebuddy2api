"""一次可计费请求的脱敏统计上下文。"""

import logging
import re
import time
from typing import Any, Callable, Iterable, Mapping, Optional

from .auth_types import AuthenticatedUser
from .stream_service import StreamObservation
from .usage_stats_middleware import USAGE_STATS_CONTEXT_STATE_KEY
from .usage_stats_store import UsageEvent, UsageStatsStore, normalize_usage, usage_stats_store

logger = logging.getLogger(__name__)


_CONTROLLED_ERROR_TYPES = frozenset({
    "authentication_error",
    "client_disconnect",
    "credential_not_found",
    "internal_error",
    "model_lookup",
    "no_credential",
    "rate_limit",
    "request_error",
    "stream_error",
    "upstream_connect_error",
    "upstream_error",
    "upstream_incomplete",
    "upstream_protocol_error",
    "upstream_server_error",
    "upstream_timeout",
    "upstream_transport_error",
    "validation_error",
})
_CONTROLLED_FINISH_REASONS = frozenset({
    "content_filter",
    "function_call",
    "length",
    "stop",
    "tool_calls",
})
_MODEL_IDENTIFIER_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}",
    re.ASCII,
)


def _normalize_error_type(error_type: Any, status_code: Optional[int]) -> str:
    """将错误收敛为受控类别，绝不持久化上游提供的任意类型文本。"""
    safe_type = error_type if isinstance(error_type, str) else ""
    if safe_type == "rate_limit_error":
        return "rate_limit"
    if safe_type in _CONTROLLED_ERROR_TYPES:
        return safe_type
    if status_code == 401:
        return "authentication_error"
    if status_code == 429:
        return "rate_limit"
    if status_code is None:
        return "stream_error"
    if status_code >= 500:
        return "upstream_error"
    return "request_error"


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))


def _thinking_mode(payload: Mapping[str, Any]) -> Optional[str]:
    thinking = payload.get("thinking")
    if isinstance(thinking, Mapping):
        thinking_type = thinking.get("type")
        if isinstance(thinking_type, str):
            normalized_type = thinking_type.strip().lower()
            if normalized_type in ("enabled", "disabled"):
                return normalized_type
    enabled = payload.get("enable_thinking")
    if isinstance(enabled, bool):
        return "enabled" if enabled else "disabled"
    return None


def _safe_model_identifier(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not _MODEL_IDENTIFIER_PATTERN.fullmatch(normalized):
        return None
    return normalized


def _normalize_finish_reason(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    if normalized in _CONTROLLED_FINISH_REASONS:
        return normalized
    return "other" if normalized else "unknown"


class UsageStatsContext:
    """仅保存允许落库的请求元数据，并消费脱敏流观察事件。"""

    def __init__(
            self,
            user: AuthenticatedUser,
            source: str,
            *,
            store: UsageStatsStore = usage_stats_store,
            time_factory: Callable[[], float] = time.time,
            monotonic_factory: Callable[[], float] = time.monotonic,
            known_models: Optional[Iterable[str]] = None,
            quota_usage_consumer: Optional[Callable[..., None]] = None,
    ) -> None:
        self.username = user.username
        self._source = source
        self._api_key_id = user.api_key_id
        self._api_key_name = user.api_key_name
        self._store = store
        self._time_factory = time_factory
        self._monotonic_factory = monotonic_factory
        self._quota_usage_consumer = quota_usage_consumer
        self._occurred_at = int(time_factory())
        self._started_monotonic = monotonic_factory()
        self._completed = False

        if known_models is None:
            from config import get_available_models
            known_models = get_available_models(user)
        self._known_models = {}
        for known_model in known_models:
            safe_model = _safe_model_identifier(known_model)
            if safe_model is None:
                continue
            self._known_models[safe_model] = safe_model
            tail = safe_model.rsplit("/", 1)[-1]
            self._known_models.setdefault(tail, tail)

        self._requested_model = "unknown"
        self._requested_model_candidate = None
        self._upstream_model = None
        self._prepared_model_candidate = None
        self._confirmed_upstream_model = None
        self._credential_id = None
        self._credential_label = None
        self._credential_generation = None
        self._client_stream = None
        self._thinking_mode = None
        self._message_count = None
        self._tool_count = None
        self._request_bytes = None
        self._retry_count = 0
        self._tool_call_count = 0
        self._finish_reason = None
        self._usage = None
        self._first_event_ms = None
        self._first_output_ms = None
        self._first_reasoning_ms = None
        self._first_content_ms = None
        self._error_type = None
        self._result_status = None
        self._client_disconnected = False

    def capture_request_bytes(self, request_bytes: int) -> None:
        """记录认证后读取到的原始请求体大小。"""
        self._request_bytes = max(0, int(request_bytes))

    def capture_request_shape(self, request_body: Mapping[str, Any]) -> None:
        """在验证前提取客户端请求形态，不保存任意对象的字符串表示。"""
        self._requested_model_candidate = _safe_model_identifier(request_body.get("model"))
        self._requested_model = self._controlled_model(
            self._requested_model_candidate
        ) or "unknown"
        self._client_stream = bool(request_body.get("stream", False))
        messages = request_body.get("messages")
        tools = request_body.get("tools")
        self._message_count = len(messages) if isinstance(messages, list) else None
        self._tool_count = len(tools) if isinstance(tools, list) else None

    def capture_prepared_request(self, prepared_payload: Mapping[str, Any]) -> None:
        """在策略准备成功后补充上游模型及最终生效的思考模式。"""
        self._prepared_model_candidate = _safe_model_identifier(prepared_payload.get("model"))
        self._upstream_model = self._controlled_model(self._prepared_model_candidate)
        if self._requested_model == "unknown" and self._upstream_model is not None:
            self._requested_model = self._upstream_model
        self._thinking_mode = _thinking_mode(prepared_payload)

    def capture_confirmed_model(self, model: Any) -> None:
        """记录来自 CodeBuddy 模型列表等可信上游来源的规范模型。"""
        safe_model = _safe_model_identifier(model)
        if safe_model is None:
            return
        canonical = safe_model.rsplit("/", 1)[-1]
        self._known_models[safe_model] = canonical
        self._known_models[canonical] = canonical
        self._confirmed_upstream_model = canonical
        self._upstream_model = canonical
        if self._requested_model == "unknown":
            self._requested_model = canonical

    def _controlled_model(self, candidate: Optional[str]) -> Optional[str]:
        if candidate is None:
            return None
        known = self._known_models.get(candidate)
        if known is not None:
            return known
        return self._known_models.get(candidate.rsplit("/", 1)[-1])

    def capture_request(
            self,
            request_body: Mapping[str, Any],
            prepared_payload: Mapping[str, Any],
            *,
            request_bytes: int,
    ) -> None:
        """组合记录完整请求元数据，供无需分阶段的调用方使用。"""
        self.capture_request_bytes(request_bytes)
        self.capture_request_shape(request_body)
        self.capture_prepared_request(prepared_payload)

    def capture_credential(
            self,
            credential_id: Optional[str],
            label: Optional[str],
            *,
            generation: Optional[int] = None,
    ) -> None:
        self._credential_id = str(credential_id) if credential_id is not None else None
        self._credential_label = str(label) if label is not None else None
        self._credential_generation = generation

    def mark_failure(self, error_type: str, result_status: Optional[int] = None) -> None:
        self._error_type = _normalize_error_type(error_type, result_status)
        self._result_status = result_status

    def mark_success(self) -> None:
        self._error_type = None
        self._result_status = 200

    def __call__(self, observation: StreamObservation) -> None:
        if self._completed:
            return
        if observation.kind == "retry":
            if observation.retry_count is not None:
                self._retry_count = max(self._retry_count, observation.retry_count)
            return
        if observation.kind == "error":
            self.mark_failure(observation.error_type or "stream_error", observation.status_code)
            return
        if observation.kind == "client_disconnect":
            self._client_disconnected = True
            return
        if observation.kind != "upstream_event":  # pragma: no cover - Literal限制外的防御
            return

        now = self._monotonic_factory()
        elapsed = _elapsed_ms(self._started_monotonic, now)
        if self._first_event_ms is None:
            self._first_event_ms = elapsed
        if observation.has_reasoning_content and self._first_reasoning_ms is None:
            self._first_reasoning_ms = elapsed
        if observation.has_content and self._first_content_ms is None:
            self._first_content_ms = elapsed
        if (
                self._first_output_ms is None
                and (
                    observation.has_reasoning_content
                    or observation.has_content
                    or observation.tool_call_count > 0
                )
        ):
            self._first_output_ms = elapsed
        self._tool_call_count += observation.tool_call_count
        if observation.finish_reason is not None:
            if not isinstance(observation.finish_reason, str) or observation.finish_reason.strip():
                self._finish_reason = _normalize_finish_reason(observation.finish_reason)
        confirmed_model = _safe_model_identifier(observation.upstream_model)
        if confirmed_model is not None:
            self._confirmed_upstream_model = confirmed_model.rsplit("/", 1)[-1]
        if observation.usage is not None:
            self._usage = normalize_usage(observation.usage)

    def complete_response(
            self,
            *,
            http_status: Optional[int],
            response_bytes: int,
            client_disconnected: bool,
    ) -> None:
        if self._completed:
            return
        self._completed = True
        completed_monotonic = self._monotonic_factory()
        disconnected = self._client_disconnected or client_disconnected
        if disconnected:
            outcome = "cancelled"
            error_type = "client_disconnect"
            result_status = None
        elif self._error_type is not None:
            outcome = "failure"
            error_type = self._error_type
            result_status = self._result_status
        elif http_status is not None and http_status >= 400:
            outcome = "failure"
            error_type = self._classify_http_error(http_status)
            result_status = http_status
        else:
            outcome = "success"
            error_type = None
            result_status = self._result_status or http_status or 200

        requested_model = self._requested_model
        upstream_model = self._upstream_model
        if outcome == "success" and self._confirmed_upstream_model is not None:
            upstream_model = self._confirmed_upstream_model
            if requested_model == "unknown":
                requested_model = upstream_model

        usage = self._usage
        event = UsageEvent(
            source=self._source,
            requested_model=requested_model,
            occurred_at=self._occurred_at,
            upstream_model=upstream_model,
            api_key_id=self._api_key_id,
            api_key_name=self._api_key_name,
            credential_id=self._credential_id,
            credential_label=self._credential_label,
            outcome=outcome,
            http_status=http_status,
            result_status=result_status,
            error_type=error_type,
            client_stream=self._client_stream,
            thinking_mode=self._thinking_mode,
            message_count=self._message_count,
            tool_count=self._tool_count,
            request_bytes=self._request_bytes,
            response_bytes=max(0, int(response_bytes)),
            retry_count=self._retry_count,
            tool_call_count=self._tool_call_count,
            finish_reason=self._finish_reason,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            cache_hit_tokens=usage.cache_hit_tokens if usage else None,
            cache_miss_tokens=usage.cache_miss_tokens if usage else None,
            cache_write_tokens=usage.cache_write_tokens if usage else None,
            credit=usage.credit if usage else None,
            duration_ms=_elapsed_ms(self._started_monotonic, completed_monotonic),
            first_event_ms=self._first_event_ms,
            first_output_ms=self._first_output_ms,
            first_reasoning_ms=self._first_reasoning_ms,
            first_content_ms=self._first_content_ms,
        )
        if (
                self._quota_usage_consumer is not None
                and event.credential_id is not None
                and event.credit is not None
        ):
            try:
                self._quota_usage_consumer(
                    self.username,
                    event.credential_id,
                    event.credit,
                    credential_generation=self._credential_generation,
                    occurred_at=event.occurred_at,
                )
            except Exception:
                logger.exception("更新凭证额度估算失败，继续写入请求统计")
        self._store.record_event(event, username=self.username)

    @staticmethod
    def _classify_http_error(status_code: int) -> str:
        if status_code in (400, 422):
            return "validation_error"
        if status_code == 401:
            return "authentication_error"
        if status_code == 429:
            return "rate_limit"
        if status_code >= 500:
            return "internal_error"
        return "request_error"


def create_usage_stats_context(
        request: Any,
        user: AuthenticatedUser,
        source: str,
        *,
        store: UsageStatsStore = usage_stats_store,
        time_factory: Callable[[], float] = time.time,
        monotonic_factory: Callable[[], float] = time.monotonic,
) -> UsageStatsContext:
    """创建上下文并挂到共享 ASGI state，供最终响应中间件收口。"""
    from .credential_quota import credential_quota_manager

    context = UsageStatsContext(
        user,
        source,
        store=store,
        time_factory=time_factory,
        monotonic_factory=monotonic_factory,
        quota_usage_consumer=credential_quota_manager.apply_usage,
    )
    setattr(request.state, USAGE_STATS_CONTEXT_STATE_KEY, context)
    return context
