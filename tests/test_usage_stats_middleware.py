import asyncio
import threading
import unittest

from starlette.requests import ClientDisconnect

from src.usage_stats_middleware import (
    DroppedCompletionEvents,
    USAGE_STATS_CONTEXT_STATE_KEY,
    UsageStatsMiddleware,
    dropped_completion_events,
)
from src.private_response import PrivateNoStoreFastAPI, PrivateNoStoreMiddleware


class RecordingContext:
    def __init__(self, username="admin", error=None):
        self.username = username
        self.error = error
        self.calls = []

    def complete_response(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error


class UsageStatsMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        dropped_completion_events.reset_for_tests()

    def test_completion_drop_counter_has_distinct_semantic_name(self):
        self.assertEqual(DroppedCompletionEvents.__name__, "DroppedCompletionEvents")

    async def test_non_http_scope_passes_through(self):
        scopes = []

        async def app(scope, _receive, _send):
            scopes.append(scope)

        scope = {"type": "lifespan"}
        await UsageStatsMiddleware(app)(scope, self._unused_receive, self._unused_send)

        self.assertEqual(scopes, [scope])

    async def test_response_without_stats_context_is_unchanged(self):
        sent = []

        async def app(_scope, _receive, send):
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def send(message):
            sent.append(message.copy())

        await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, send)

        self.assertEqual([item["type"] for item in sent], ["http.response.start", "http.response.body"])
        self.assertEqual(dropped_completion_events.get("admin"), 0)

    async def test_context_attached_downstream_receives_final_status_and_sent_bytes(self):
        context = RecordingContext()
        sent = []

        async def app(scope, _receive, send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"abc", "more_body": True})
            await send({"type": "http.response.body", "body": b"de", "more_body": False})

        async def send(message):
            sent.append(message.copy())

        await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, send)

        self.assertEqual(len(sent), 3)
        self.assertEqual(
            context.calls,
            [{"http_status": 200, "response_bytes": 5, "client_disconnected": False}],
        )

    async def test_unrelated_asgi_send_message_does_not_change_response_metadata(self):
        context = RecordingContext()
        sent = []

        async def app(scope, _receive, send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            await send({"type": "http.response.trailers", "headers": []})

        async def send(message):
            sent.append(message.copy())

        await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, send)

        self.assertEqual(sent, [{"type": "http.response.trailers", "headers": []}])
        self.assertEqual(
            context.calls,
            [{"http_status": None, "response_bytes": 0, "client_disconnected": False}],
        )

    async def test_disconnect_received_is_reported(self):
        context = RecordingContext()

        async def app(scope, receive, _send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            self.assertEqual(await receive(), {"type": "http.disconnect"})

        async def receive():
            return {"type": "http.disconnect"}

        await UsageStatsMiddleware(app)(self._scope(), receive, self._unused_send)

        self.assertEqual(
            context.calls,
            [{"http_status": None, "response_bytes": 0, "client_disconnected": True}],
        )

    async def test_disconnect_after_final_body_does_not_override_success(self):
        context = RecordingContext()

        async def app(scope, receive, send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"done", "more_body": False})
            self.assertEqual(await receive(), {"type": "http.disconnect"})

        async def receive():
            return {"type": "http.disconnect"}

        await UsageStatsMiddleware(app)(self._scope(), receive, self._unused_send)

        self.assertEqual(
            context.calls,
            [{"http_status": 200, "response_bytes": 4, "client_disconnected": False}],
        )

    async def test_disconnect_exception_after_final_body_does_not_override_success(self):
        context = RecordingContext()

        async def app(scope, _receive, send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"done", "more_body": False})
            raise ClientDisconnect()

        with self.assertRaises(ClientDisconnect):
            await UsageStatsMiddleware(app)(
                self._scope(),
                self._unused_receive,
                self._unused_send,
            )

        self.assertEqual(
            context.calls,
            [{"http_status": 200, "response_bytes": 4, "client_disconnected": False}],
        )

    async def test_regular_request_message_does_not_mark_disconnect(self):
        context = RecordingContext()

        async def app(scope, receive, _send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            self.assertEqual(
                await receive(),
                {"type": "http.request", "body": b"request", "more_body": False},
            )

        async def receive():
            return {"type": "http.request", "body": b"request", "more_body": False}

        await UsageStatsMiddleware(app)(self._scope(), receive, self._unused_send)

        self.assertEqual(
            context.calls,
            [{"http_status": None, "response_bytes": 0, "client_disconnected": False}],
        )

    async def test_application_error_is_reraised_after_context_is_completed(self):
        context = RecordingContext()

        async def app(scope, _receive, _send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, self._unused_send)

        self.assertEqual(
            context.calls,
            [{"http_status": None, "response_bytes": 0, "client_disconnected": False}],
        )

    async def test_stats_completion_failure_does_not_change_response(self):
        context = RecordingContext(error=RuntimeError("database unavailable"))
        sent = []

        async def app(scope, _receive, send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        async def send(message):
            sent.append(message.copy())

        with self.assertLogs("src.usage_stats_middleware", level="ERROR"):
            await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, send)

        self.assertEqual(sent[-1]["body"], b"ok")
        self.assertEqual(dropped_completion_events.get("admin"), 1)
        self.assertEqual(dropped_completion_events.get("alice"), 0)

    async def test_async_completion_is_supported_and_send_failure_bytes_are_not_counted(self):
        class AsyncContext(RecordingContext):
            async def complete_response(self, **kwargs):
                self.calls.append(kwargs)

        context = AsyncContext()

        async def app(scope, _receive, send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"not-sent", "more_body": False})

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("socket closed")

        with self.assertRaisesRegex(OSError, "socket closed"):
            await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, send)

        self.assertEqual(
            context.calls,
            [{"http_status": 200, "response_bytes": 0, "client_disconnected": True}],
        )

    async def test_sync_completion_runs_off_event_loop_and_async_completion_stays_on_it(self):
        event_loop_thread = threading.get_ident()

        class SyncContext(RecordingContext):
            def complete_response(self, **kwargs):
                self.thread_id = threading.get_ident()
                super().complete_response(**kwargs)

        class AsyncContext(RecordingContext):
            async def complete_response(self, **kwargs):
                self.thread_id = threading.get_ident()
                self.calls.append(kwargs)

        contexts = [SyncContext(), AsyncContext()]
        for context in contexts:
            async def app(scope, _receive, _send, current=context):
                scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = current

            await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, self._unused_send)

        self.assertNotEqual(contexts[0].thread_id, event_loop_thread)
        self.assertEqual(contexts[1].thread_id, event_loop_thread)

    async def test_sync_completion_returning_awaitable_is_fully_awaited(self):
        finished = asyncio.Event()

        class AwaitableContext(RecordingContext):
            def complete_response(self, **kwargs):
                self.calls.append(kwargs)

                async def finish():
                    finished.set()

                return finish()

        context = AwaitableContext()

        async def app(scope, _receive, _send):
            scope["state"][USAGE_STATS_CONTEXT_STATE_KEY] = context

        await UsageStatsMiddleware(app)(self._scope(), self._unused_receive, self._unused_send)

        self.assertTrue(finished.is_set())

    def test_private_fastapi_wraps_complete_stack_with_stats_then_no_store(self):
        stack = PrivateNoStoreFastAPI().build_middleware_stack()

        self.assertIsInstance(stack, PrivateNoStoreMiddleware)
        self.assertIsInstance(stack.app, UsageStatsMiddleware)

    @staticmethod
    def _scope():
        return {"type": "http", "method": "GET", "path": "/", "state": {}}

    @staticmethod
    async def _unused_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    @staticmethod
    async def _unused_send(_message):
        return None


if __name__ == "__main__":
    unittest.main()
