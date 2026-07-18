import unittest
from unittest import mock

from src.auth_types import AuthenticatedUser
from src.stream_service import StreamObservation
from src.usage_stats_context import (
    UsageStatsContext,
    _safe_model_identifier,
    _thinking_mode,
    create_usage_stats_context,
)
from src.usage_stats_middleware import USAGE_STATS_CONTEXT_STATE_KEY


class FakeStore:
    def __init__(self, error=None):
        self.events = []
        self.error = error

    def record_event(self, event, *, username):
        if self.error:
            raise self.error
        self.username = username
        self.events.append(event)


class UsageStatsContextTests(unittest.TestCase):
    def test_thinking_mode_only_accepts_normalized_controlled_values(self):
        cases = [
            ({"thinking": {"type": " ENABLED "}}, "enabled"),
            ({"thinking": {"type": "Disabled"}}, "disabled"),
            ({"thinking": {"type": "private-mode"}, "enable_thinking": True}, "enabled"),
            ({"thinking": {"type": 123}, "enable_thinking": False}, "disabled"),
            ({"thinking": "invalid"}, None),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(_thinking_mode(payload), expected)

    def test_model_identifier_rejects_object_stringification_and_non_identifier_text(self):
        cases = [
            ("provider/model-a:preview@v1+fast", "provider/model-a:preview@v1+fast"),
            ({"private": "value"}, None),
            ("{'private': 'upstream'}", None),
            ("model private prompt", None),
            ("a" * 201, None),
            ("模型", None),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(_safe_model_identifier(value), expected)

    def test_success_collects_only_sanitized_metadata_usage_and_timings(self):
        store = FakeStore()
        monotonic = mock.Mock(side_effect=[10.0, 10.1, 10.3, 10.4, 10.5])
        context = UsageStatsContext(
            AuthenticatedUser(
                username="admin",
                source="api_key",
                api_key_id="key-1",
                api_key_name="client",
            ),
            "external_api",
            store=store,
            time_factory=lambda: 1_000,
            monotonic_factory=monotonic,
        )
        request_body = {
            "model": "provider/glm",
            "stream": True,
            "messages": [{"role": "user", "content": "private prompt"}],
            "tools": [{"function": {"name": "private-tool"}}],
        }
        prepared_payload = {
            **request_body,
            "model": "glm",
            "thinking": {"type": "enabled"},
        }

        context.capture_request(request_body, prepared_payload, request_bytes=321)
        context.capture_credential("credential-1", "credential.json")
        context(StreamObservation(
            kind="upstream_event",
            has_reasoning_content=True,
        ))
        context(StreamObservation(
            kind="upstream_event",
            has_content=True,
            tool_call_count=1,
            finish_reason="stop",
            upstream_model="glm",
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
                "completion_tokens_details": {"reasoning_tokens": 5},
                "prompt_cache_hit_tokens": 3,
                "prompt_cache_miss_tokens": 8,
                "credit": 0.25,
            },
        ))
        context(StreamObservation(kind="upstream_event", upstream_done=True))
        context.complete_response(http_status=200, response_bytes=456, client_disconnected=False)

        self.assertEqual(len(store.events), 1)
        self.assertEqual(store.username, "admin")
        event = store.events[0]
        self.assertEqual(event.source, "external_api")
        self.assertEqual(event.occurred_at, 1_000)
        self.assertEqual(event.requested_model, "glm")
        self.assertEqual(event.upstream_model, "glm")
        self.assertEqual(event.api_key_id, "key-1")
        self.assertEqual(event.api_key_name, "client")
        self.assertEqual(event.credential_id, "credential-1")
        self.assertEqual(event.credential_label, "credential.json")
        self.assertEqual(event.outcome, "success")
        self.assertEqual(event.http_status, 200)
        self.assertEqual(event.result_status, 200)
        self.assertIs(event.client_stream, True)
        self.assertEqual(event.thinking_mode, "enabled")
        self.assertEqual(event.message_count, 1)
        self.assertEqual(event.tool_count, 1)
        self.assertEqual(event.request_bytes, 321)
        self.assertEqual(event.response_bytes, 456)
        self.assertEqual(event.tool_call_count, 1)
        self.assertEqual(event.finish_reason, "stop")
        self.assertEqual(event.input_tokens, 11)
        self.assertEqual(event.output_tokens, 7)
        self.assertEqual(event.total_tokens, 18)
        self.assertEqual(event.reasoning_tokens, 5)
        self.assertEqual(event.cache_hit_tokens, 3)
        self.assertEqual(event.cache_miss_tokens, 8)
        self.assertEqual(event.credit, 0.25)
        self.assertEqual(event.first_event_ms, 100)
        self.assertEqual(event.first_reasoning_ms, 100)
        self.assertEqual(event.first_output_ms, 100)
        self.assertEqual(event.first_content_ms, 300)
        self.assertEqual(event.duration_ms, 500)
        serialized = repr(vars(event))
        self.assertNotIn("private prompt", serialized)
        self.assertNotIn("private-tool", serialized)

    def test_observed_credit_updates_quota_once_even_when_request_failed(self):
        store = FakeStore()
        quota_consumer = mock.Mock()
        context = UsageStatsContext(
            AuthenticatedUser("admin", "api_key"),
            "external_api",
            store=store,
            quota_usage_consumer=quota_consumer,
            time_factory=lambda: 1_234,
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.1, 1.2]),
        )
        context.capture_credential("credential-1", "credential.json")
        context(StreamObservation(kind="upstream_event", usage={"credit": 0.75}))
        context.mark_failure("upstream_error", 502)

        context.complete_response(http_status=502, response_bytes=0, client_disconnected=False)
        context.complete_response(http_status=200, response_bytes=1, client_disconnected=False)

        quota_consumer.assert_called_once_with(
            "admin", "credential-1", 0.75, occurred_at=1_234,
        )

    def test_quota_update_failure_does_not_drop_usage_event(self):
        store = FakeStore()
        quota_consumer = mock.Mock(side_effect=RuntimeError("quota failed"))
        context = UsageStatsContext(
            AuthenticatedUser("admin", "api_key"),
            "external_api",
            store=store,
            quota_usage_consumer=quota_consumer,
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.1, 1.2]),
        )
        context.capture_credential("credential-1", "credential.json")
        context(StreamObservation(kind="upstream_event", usage={"credit": 0.5}))

        with self.assertLogs("src.usage_stats_context", level="ERROR"):
            context.complete_response(http_status=200, response_bytes=1, client_disconnected=False)

        self.assertEqual(store.events[0].credit, 0.5)

    def test_request_metadata_can_be_captured_before_validation_and_preparation(self):
        store = FakeStore()
        context = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=store,
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.1]),
        )

        context.capture_request_bytes(17)
        context.capture_request_shape({
            "model": "client/model",
            "stream": True,
            "messages": [],
            "tools": [{"private": "must not persist"}],
        })
        context.capture_prepared_request({
            "model": "model",
            "thinking": {"type": "ENABLED"},
        })
        context.complete_response(http_status=422, response_bytes=3, client_disconnected=False)

        event = store.events[0]
        self.assertEqual(event.request_bytes, 17)
        self.assertEqual(event.requested_model, "unknown")
        self.assertIsNone(event.upstream_model)
        self.assertIs(event.client_stream, True)
        self.assertEqual(event.message_count, 0)
        self.assertEqual(event.tool_count, 1)
        self.assertEqual(event.thinking_mode, "enabled")
        self.assertNotIn("must not persist", repr(event))

    def test_request_shape_rejects_non_string_model_as_unknown(self):
        store = FakeStore()
        context = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=store,
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.1]),
        )

        context.capture_request_shape({"model": {"private": "value"}})
        context.capture_prepared_request({"model": {"private": "upstream"}})
        context.complete_response(http_status=400, response_bytes=0, client_disconnected=False)

        self.assertEqual(store.events[0].requested_model, "unknown")
        self.assertIsNone(store.events[0].upstream_model)
        self.assertNotIn("private", repr(store.events[0]))

    def test_finish_reason_is_restricted_to_controlled_categories(self):
        cases = [
            ("STOP", "stop"),
            ("length", "length"),
            ("tool_calls", "tool_calls"),
            ("content_filter", "content_filter"),
            ("function_call", "function_call"),
            ("private-upstream-text", "other"),
            ("", None),
            (123, "unknown"),
        ]
        for finish_reason, expected in cases:
            with self.subTest(finish_reason=finish_reason):
                store = FakeStore()
                context = UsageStatsContext(
                    AuthenticatedUser("admin", "session_cookie"),
                    "admin_playground",
                    store=store,
                    monotonic_factory=mock.Mock(side_effect=[1.0, 1.1, 1.2]),
                )
                context(StreamObservation(
                    kind="upstream_event",
                    finish_reason=finish_reason,
                ))
                context.complete_response(
                    http_status=200,
                    response_bytes=0,
                    client_disconnected=False,
                )

                self.assertEqual(store.events[0].finish_reason, expected)
                self.assertNotIn("private-upstream-text", repr(store.events[0]))

    def test_retry_error_and_disconnect_have_stable_precedence_and_complete_once(self):
        store = FakeStore()
        context = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=store,
            time_factory=lambda: 2_000,
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.2]),
        )
        context.capture_request(
            {"messages": [], "enable_thinking": False},
            {"model": "default", "messages": [], "enable_thinking": False},
            request_bytes=2,
        )
        context(StreamObservation(kind="retry", retry_count=2, error_type="upstream_connect_error"))
        context(StreamObservation(kind="error", error_type="upstream_timeout", status_code=504))
        context(StreamObservation(kind="client_disconnect"))

        context.complete_response(http_status=200, response_bytes=10, client_disconnected=True)
        context.complete_response(http_status=200, response_bytes=99, client_disconnected=False)

        event = store.events[0]
        self.assertEqual(len(store.events), 1)
        self.assertEqual(event.requested_model, "unknown")
        self.assertIsNone(event.upstream_model)
        self.assertEqual(event.outcome, "cancelled")
        self.assertEqual(event.error_type, "client_disconnect")
        self.assertEqual(event.http_status, 200)
        self.assertIsNone(event.result_status)
        self.assertEqual(event.retry_count, 2)
        self.assertEqual(event.response_bytes, 10)
        self.assertEqual(event.thinking_mode, "disabled")
        self.assertEqual(event.duration_ms, 200)

    def test_http_failure_and_explicit_result_are_classified_without_upstream_events(self):
        cases = [
            (422, None, None, "validation_error", 422),
            (401, None, None, "authentication_error", 401),
            (429, None, None, "rate_limit", 429),
            (503, None, None, "internal_error", 503),
            (403, None, None, "request_error", 403),
            (401, "no_credential", 401, "no_credential", 401),
            (500, "model_lookup", 502, "model_lookup", 502),
        ]
        for http_status, marked_type, marked_status, expected_type, expected_status in cases:
            with self.subTest(http_status=http_status, marked_type=marked_type):
                store = FakeStore()
                context = UsageStatsContext(
                    AuthenticatedUser("admin", "session_cookie"),
                    "credential_test",
                    store=store,
                    time_factory=lambda: 3_000,
                    monotonic_factory=mock.Mock(side_effect=[2.0, 2.01]),
                )
                if marked_type:
                    context.mark_failure(marked_type, marked_status)
                context.complete_response(
                    http_status=http_status,
                    response_bytes=0,
                    client_disconnected=False,
                )
                event = store.events[0]
                self.assertEqual(event.outcome, "failure")
                self.assertEqual(event.error_type, expected_type)
                self.assertEqual(event.result_status, expected_status)

    def test_failure_error_type_is_restricted_to_controlled_categories(self):
        cases = [
            ("validation_error", 422, "validation_error"),
            ("rate_limit_error", 429, "rate_limit"),
            ("quota_error-private-detail", 401, "authentication_error"),
            ("quota_error-private-detail", 429, "rate_limit"),
            ("quota_error-private-detail", 502, "upstream_error"),
            ("quota_error-private-detail", 403, "request_error"),
            ("quota_error-private-detail", None, "stream_error"),
        ]
        for error_type, status_code, expected in cases:
            with self.subTest(error_type=error_type, status_code=status_code):
                store = FakeStore()
                context = UsageStatsContext(
                    AuthenticatedUser("admin", "session_cookie"),
                    "credential_test",
                    store=store,
                    monotonic_factory=mock.Mock(side_effect=[1.0, 1.1]),
                )

                context.mark_failure(error_type, status_code)
                context.complete_response(
                    http_status=status_code,
                    response_bytes=0,
                    client_disconnected=False,
                )

                self.assertEqual(store.events[0].error_type, expected)
                self.assertNotIn("private-detail", repr(store.events[0]))

    def test_request_shape_edge_cases_mark_success_and_ignore_late_observations(self):
        store = FakeStore()
        context = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=store,
            known_models=("fallback",),
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.1]),
        )
        context.capture_request(
            {"model": "", "messages": "not-a-list", "tools": None},
            {"model": "fallback", "thinking": {"type": ""}, "enable_thinking": True},
            request_bytes=-1,
        )
        context.capture_credential(None, None)
        context(StreamObservation(kind="retry"))
        context.mark_success()
        context.complete_response(http_status=None, response_bytes=-1, client_disconnected=False)
        context(StreamObservation(kind="retry", retry_count=99))

        event = store.events[0]
        self.assertEqual(event.requested_model, "fallback")
        self.assertEqual(event.upstream_model, "fallback")
        self.assertEqual(event.thinking_mode, "enabled")
        self.assertIsNone(event.message_count)
        self.assertIsNone(event.tool_count)
        self.assertEqual(event.request_bytes, 0)
        self.assertEqual(event.response_bytes, 0)
        self.assertEqual(event.result_status, 200)
        self.assertEqual(event.retry_count, 0)
        self.assertIsNone(event.credential_id)
        self.assertIsNone(event.credential_label)

        without_thinking = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=FakeStore(),
            monotonic_factory=mock.Mock(side_effect=[2.0, 2.1]),
        )
        without_thinking.capture_request({}, {"thinking": "invalid"}, request_bytes=0)
        without_thinking.complete_response(
            http_status=200,
            response_bytes=0,
            client_disconnected=False,
        )
        self.assertIsNone(without_thinking._store.events[0].thinking_mode)

    def test_arbitrary_models_are_unknown_until_a_successful_upstream_confirmation(self):
        failed_store = FakeStore()
        failed = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=failed_store,
            known_models=("configured-model",),
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.1]),
        )
        failed.capture_request_shape({"model": "sk-token-shaped-model"})
        failed.capture_prepared_request({"model": "sk-token-shaped-model"})
        failed.complete_response(http_status=400, response_bytes=0, client_disconnected=False)

        successful_store = FakeStore()
        successful = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=successful_store,
            known_models=("configured-model",),
            monotonic_factory=mock.Mock(side_effect=[2.0, 2.1, 2.2]),
        )
        successful.capture_request_shape({"model": "provider/dynamic-model"})
        successful.capture_prepared_request({"model": "dynamic-model"})
        successful(StreamObservation(
            kind="upstream_event",
            has_content=True,
            upstream_model="dynamic-model",
        ))
        successful.complete_response(
            http_status=200,
            response_bytes=1,
            client_disconnected=False,
        )

        known_store = FakeStore()
        known = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "admin_playground",
            store=known_store,
            known_models=("configured-model",),
            monotonic_factory=mock.Mock(side_effect=[3.0, 3.1]),
        )
        known.capture_request_shape({"model": "provider/configured-model"})
        known.capture_prepared_request({"model": "configured-model"})
        known.complete_response(http_status=422, response_bytes=0, client_disconnected=False)

        self.assertEqual(failed_store.events[0].requested_model, "unknown")
        self.assertIsNone(failed_store.events[0].upstream_model)
        self.assertEqual(successful_store.events[0].requested_model, "dynamic-model")
        self.assertEqual(successful_store.events[0].upstream_model, "dynamic-model")
        self.assertEqual(known_store.events[0].requested_model, "configured-model")
        self.assertEqual(known_store.events[0].upstream_model, "configured-model")

    def test_trusted_model_confirmation_rejects_invalid_values_and_canonicalizes_namespaces(self):
        unknown_store = FakeStore()
        unknown = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "credential_test",
            store=unknown_store,
            known_models=({}, "configured-model"),
            monotonic_factory=mock.Mock(side_effect=[1.0, 1.1]),
        )
        unknown.capture_request_shape({"model": "arbitrary-model"})
        unknown.capture_confirmed_model({"private": "value"})
        unknown.capture_confirmed_model("provider/dynamic-model")
        unknown.complete_response(
            http_status=200,
            response_bytes=0,
            client_disconnected=False,
        )

        known_store = FakeStore()
        known = UsageStatsContext(
            AuthenticatedUser("admin", "session_cookie"),
            "credential_test",
            store=known_store,
            known_models=("configured-model",),
            monotonic_factory=mock.Mock(side_effect=[2.0, 2.1]),
        )
        known.capture_request_shape({"model": "configured-model"})
        known.capture_confirmed_model("configured-model")
        known.complete_response(
            http_status=200,
            response_bytes=0,
            client_disconnected=False,
        )

        self.assertEqual(unknown_store.events[0].requested_model, "dynamic-model")
        self.assertEqual(unknown_store.events[0].upstream_model, "dynamic-model")
        self.assertEqual(known_store.events[0].requested_model, "configured-model")
        self.assertEqual(known_store.events[0].upstream_model, "configured-model")

    def test_context_factory_attaches_to_shared_request_state_and_store_error_is_visible(self):
        class Request:
            def __init__(self):
                self.state = type("State", (), {})()

        request = Request()
        store = FakeStore(RuntimeError("write failed"))
        context = create_usage_stats_context(
            request,
            AuthenticatedUser("admin", "session_cookie"),
            "credential_test",
            store=store,
            time_factory=lambda: 4_000,
            monotonic_factory=mock.Mock(side_effect=[3.0, 3.1]),
        )

        self.assertIs(getattr(request.state, USAGE_STATS_CONTEXT_STATE_KEY), context)
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            context.complete_response(
                http_status=200,
                response_bytes=0,
                client_disconnected=False,
            )


if __name__ == "__main__":
    unittest.main()
