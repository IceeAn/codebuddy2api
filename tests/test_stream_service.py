import asyncio
import json
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

import src.stream_service as stream_service
from src.auth_types import AuthenticatedUser
from src.openai_compat import CodeBuddyResponseEvent, CompletionResponseContext
from src.openai_router import list_v1_models
from src.stream_service import (
    AppLifecycleManager,
    CodeBuddyStreamService,
    HTTP_CLIENT_CONFIG,
    SSEConnectionManager,
    SecurityConfig,
    StreamResponseAggregator,
    UpstreamAPIError,
    close_http_client,
    get_codebuddy_api_url,
    get_http_client,
)

from tests.helpers import FakeHttpClient


class OpenAIRouterModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_list_returns_minimal_openai_model_objects(self):
        async def fake_models(_user):
            return ["deepseek-v4-pro", "glm-5.1"]

        with mock.patch("src.openai_router.get_available_models_list", fake_models):
            response = await list_v1_models(AuthenticatedUser(username="admin", source="users_file"))

        models = {item["id"]: item for item in response["data"]}

        self.assertEqual(models["deepseek-v4-pro"]["object"], "model")
        self.assertEqual(models["deepseek-v4-pro"]["owned_by"], "codebuddy")
        self.assertIsInstance(models["deepseek-v4-pro"]["created"], int)
        self.assertNotIn("reasoning", models["deepseek-v4-pro"])
        self.assertNotIn("limit", models["glm-5.1"])


class HttpClientConfigTests(unittest.TestCase):
    def test_global_http_client_ignores_proxy_environment(self):
        self.assertIs(HTTP_CLIENT_CONFIG["trust_env"], False)

    def test_api_url_uses_current_configured_endpoint(self):
        with mock.patch("config.get_codebuddy_api_endpoint", return_value="https://api.example"):
            self.assertEqual(get_codebuddy_api_url(), "https://api.example/v2/chat/completions")

    def test_security_config_returns_disabled_ssl_setting(self):
        with mock.patch("config.get_ssl_verify", return_value=False):
            self.assertFalse(SecurityConfig.get_ssl_verify())


class HttpClientPoolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_pool = stream_service._http_client_pool
        stream_service._http_client_pool = None

    async def asyncTearDown(self):
        stream_service._http_client_pool = self.original_pool

    async def test_get_http_client_creates_and_reuses_singleton(self):
        client = mock.Mock()
        with mock.patch("src.stream_service.httpx.AsyncClient", return_value=client) as client_class:
            first = await get_http_client()
            second = await get_http_client()

        self.assertIs(first, client)
        self.assertIs(second, client)
        client_class.assert_called_once_with(**HTTP_CLIENT_CONFIG)

    async def test_get_http_client_reuses_pool_initialized_while_waiting_for_lock(self):
        client = mock.Mock()

        class InitializingLock:
            async def __aenter__(self):
                stream_service._http_client_pool = client

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

        with (
            mock.patch.object(stream_service, "_client_lock", InitializingLock()),
            mock.patch("src.stream_service.httpx.AsyncClient") as client_class,
        ):
            result = await get_http_client()

        self.assertIs(result, client)
        client_class.assert_not_called()

    async def test_close_http_client_closes_existing_pool_and_ignores_empty_pool(self):
        await close_http_client()
        client = mock.Mock()
        client.aclose = mock.AsyncMock()
        stream_service._http_client_pool = client

        await close_http_client()

        client.aclose.assert_awaited_once_with()
        self.assertIsNone(stream_service._http_client_pool)

    async def test_lifecycle_delegates_to_pool_functions(self):
        with (
            mock.patch("src.stream_service.get_http_client", new=mock.AsyncMock()) as get_client,
            mock.patch("src.stream_service.close_http_client", new=mock.AsyncMock()) as close_client,
        ):
            await AppLifecycleManager.startup()
            await AppLifecycleManager.shutdown()

        get_client.assert_awaited_once_with()
        close_client.assert_awaited_once_with()


class SSEConnectionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_with_retry_rejects_negative_retry_count(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            SSEConnectionManager(max_connect_retries=-1)

    async def test_stream_with_retry_yields_successful_stream(self):
        async def successful_stream(value):
            yield value

        chunks = [
            chunk async for chunk in SSEConnectionManager().stream_with_retry(successful_stream, "ok")
        ]

        self.assertEqual(chunks, ["ok"])

    async def test_stream_with_retry_recovers_from_connect_error_with_jitter(self):
        attempts = 0

        async def flaky_stream():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("offline")
            yield "recovered"

        with mock.patch("src.stream_service.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            chunks = [
                chunk async for chunk in SSEConnectionManager(
                    max_connect_retries=1,
                    retry_delay=0.25,
                    jitter_ratio=0.2,
                    random_source=lambda: 0.5,
                ).stream_with_retry(flaky_stream)
            ]

        self.assertEqual(chunks, ["recovered"])
        sleep.assert_awaited_once_with(0.275)

    async def test_stream_with_retry_raises_exhausted_connect_error_without_error_chunk(self):
        async def failed_stream():
            raise httpx.ConnectError("offline")
            yield

        with self.assertRaises(httpx.ConnectError):
            await anext(SSEConnectionManager(max_connect_retries=0).stream_with_retry(failed_stream))

    async def test_stream_with_retry_does_not_replay_ambiguous_transport_errors(self):
        errors = [
            httpx.ReadTimeout("read timeout"),
            httpx.WriteError("partial request"),
            httpx.RemoteProtocolError("incomplete response"),
        ]

        for error in errors:
            with self.subTest(error=error):
                attempts = 0

                async def failed_stream():
                    nonlocal attempts
                    attempts += 1
                    raise error
                    yield

                with self.assertRaises(type(error)):
                    await anext(SSEConnectionManager().stream_with_retry(failed_stream))
                self.assertEqual(attempts, 1)

    async def test_stream_with_retry_raises_unexpected_error_without_error_chunk(self):
        async def failed_stream():
            raise RuntimeError("broken stream")
            yield

        with self.assertRaises(RuntimeError):
            await anext(SSEConnectionManager().stream_with_retry(failed_stream))

    async def test_stream_with_retry_does_not_replay_after_first_emitted_chunk(self):
        attempts = 0

        async def interrupted_stream():
            nonlocal attempts
            attempts += 1
            yield "partial"
            raise httpx.ConnectError("connection lost")

        iterator = SSEConnectionManager(max_connect_retries=3, retry_delay=0).stream_with_retry(interrupted_stream)

        self.assertEqual(await anext(iterator), "partial")
        with self.assertRaises(httpx.ConnectError):
            await anext(iterator)
        self.assertEqual(attempts, 1)

    async def test_stream_with_retry_closes_active_inner_stream(self):
        inner_closed = False

        async def open_stream():
            nonlocal inner_closed
            try:
                yield "partial"
                await asyncio.Event().wait()
            finally:
                inner_closed = True

        iterator = SSEConnectionManager().stream_with_retry(open_stream)
        self.assertEqual(await anext(iterator), "partial")

        await iterator.aclose()

        self.assertTrue(inner_closed)

    async def test_run_with_retry_applies_same_connect_policy_to_non_stream_operations(self):
        attempts = 0

        async def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectTimeout("connect timeout")
            return "ok"

        with mock.patch("src.stream_service.asyncio.sleep", new=mock.AsyncMock()):
            result = await SSEConnectionManager(retry_delay=0).run_with_retry(operation)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, 2)

    async def test_run_with_retry_raises_when_connect_retries_are_exhausted(self):
        async def operation():
            raise httpx.ConnectError("offline")

        with self.assertRaises(httpx.ConnectError):
            await SSEConnectionManager(max_connect_retries=0).run_with_retry(operation)


class StreamResponseAggregatorTests(unittest.TestCase):
    @staticmethod
    def _context():
        return CompletionResponseContext("chatcmpl-local", 123, "client-model")

    @staticmethod
    def _process_chunk(aggregator, chunk):
        aggregator.process_event(CodeBuddyResponseEvent.parse(chunk))

    def test_aggregator_preserves_usage_and_system_fingerprint(self):
        aggregator = StreamResponseAggregator(self._context())

        self._process_chunk(aggregator, {
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        })
        self._process_chunk(aggregator, {
            "id": "chatcmpl-1",
            "model": "glm-5.2",
            "system_fingerprint": "fp",
            "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}],
        })
        response = aggregator.finalize()

        self.assertEqual(response["id"], "chatcmpl-local")
        self.assertEqual(response["created"], 123)
        self.assertEqual(response["model"], "client-model")
        self.assertEqual(response["usage"], {"prompt_tokens": 1, "completion_tokens": 2})
        self.assertEqual(response["system_fingerprint"], "fp")

    def test_aggregator_merges_reasoning_content_and_existing_tool_call_fragments(self):
        aggregator = StreamResponseAggregator(self._context())
        self._process_chunk(aggregator, {
            "choices": [{
                "delta": {
                    "reasoning_content": "think ",
                    "content": "answer ",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"q":'},
                    }],
                },
                "finish_reason": None,
            }],
        })
        self._process_chunk(aggregator, {
            "choices": [{
                "delta": {
                    "reasoning_content": "more",
                    "content": "done",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search", "arguments": '"x"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        })

        response = aggregator.finalize()
        message = response["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], "think more")
        self.assertEqual(message["content"], "answer done")
        self.assertEqual(message["tool_calls"][0]["function"], {
            "name": "search",
            "arguments": '{"q":"x"}',
        })

    def test_aggregator_handles_unambiguous_continuation_without_id(self):
        aggregator = StreamResponseAggregator(self._context())
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"tool_calls": [{"id": "call_1", "function": {}}]},
            "finish_reason": None,
        }]})
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"tool_calls": [{"function": {}}]},
            "finish_reason": None,
        }]})
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"tool_calls": [{"function": {"name": "lookup", "arguments": "{}"}}]},
            "finish_reason": "tool_calls",
        }]})

        response = aggregator.finalize()

        self.assertEqual(response["choices"][0]["message"]["tool_calls"][0]["function"], {
            "name": "lookup",
            "arguments": "{}",
        })

    def test_aggregator_orders_parallel_tools_by_upstream_index(self):
        aggregator = StreamResponseAggregator(self._context())
        self._process_chunk(aggregator, {
            "choices": [{
                "delta": {"tool_calls": [
                    {"id": "call_2", "index": 1, "function": {"name": "b", "arguments": "{}"}},
                    {"id": "call_1", "index": 0, "function": {"name": "a", "arguments": "{}"}},
                ]},
                "finish_reason": "tool_calls",
            }],
        })

        response = aggregator.finalize()

        self.assertEqual(
            [tool["id"] for tool in response["choices"][0]["message"]["tool_calls"]],
            ["call_1", "call_2"],
        )

    def test_aggregator_keeps_sparse_and_generated_tool_indexes_distinct(self):
        aggregator = StreamResponseAggregator(self._context())
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"tool_calls": [{
                "id": "call_a",
                "index": 1,
                "function": {"name": "a", "arguments": "A"},
            }]},
            "finish_reason": None,
        }]})
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"tool_calls": [{
                "id": "call_b",
                "function": {"name": "b", "arguments": "B"},
            }]},
            "finish_reason": "tool_calls",
        }]})

        tool_calls = aggregator.finalize()["choices"][0]["message"]["tool_calls"]

        self.assertEqual([tool["id"] for tool in tool_calls], ["call_b", "call_a"])
        self.assertEqual([tool["function"]["arguments"] for tool in tool_calls], ["B", "A"])

    def test_aggregator_preserves_upstream_finish_reason_when_tools_exist(self):
        aggregator = StreamResponseAggregator(self._context())
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"tool_calls": [{
                "id": "call_1",
                "index": 0,
                "function": {"name": "lookup", "arguments": "partial"},
            }]},
            "finish_reason": "length",
        }]})

        response = aggregator.finalize()

        self.assertEqual(response["choices"][0]["finish_reason"], "length")

    def test_aggregator_ignores_unrecognized_delta_values_without_failing(self):
        aggregator = StreamResponseAggregator(self._context())
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"content": {"unexpected": True}, "tool_calls": ["unexpected"]},
            "finish_reason": "stop",
        }]})

        response = aggregator.finalize()

        self.assertEqual(response["choices"][0]["message"]["content"], "")
        self.assertNotIn("tool_calls", response["choices"][0]["message"])

    def test_aggregator_ignores_tool_fragments_without_context_or_function_object(self):
        aggregator = StreamResponseAggregator(self._context())
        self._process_chunk(aggregator, {"choices": [{
            "delta": {"tool_calls": [
                {"function": {"arguments": "orphan"}},
                {"id": "call_bad", "index": 0, "function": "unexpected"},
            ]},
            "finish_reason": "stop",
        }]})

        response = aggregator.finalize()

        self.assertNotIn("tool_calls", response["choices"][0]["message"])


class StreamServiceErrorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _prefetch_stream(service, payload=None):
        request_payload = {"model": "model"} if payload is None else payload
        response = await service.handle_stream_response(request_payload, {})
        return await anext(response.body_iterator)

    def test_handle_api_error_maps_status_classes(self):
        service = CodeBuddyStreamService()
        cases = [
            (401, 401, "teapot", "authentication_error"),
            (429, 429, "teapot", "rate_limit_error"),
            (500, 502, "teapot", "upstream_server_error"),
            (418, 418, "teapot", "upstream_error"),
            (302, 502, "unexpected status", "upstream_protocol_error"),
        ]

        for upstream_status, expected_status, detail, error_type in cases:
            with self.subTest(upstream_status=upstream_status):
                with self.assertRaises(HTTPException) as raised:
                    service._handle_api_error(upstream_status, "teapot")
                self.assertEqual(raised.exception.status_code, expected_status)
                self.assertIn(detail, raised.exception.detail)
                self.assertIsInstance(raised.exception, UpstreamAPIError)
                self.assertEqual(raised.exception.error["type"], error_type)

    def test_handle_api_error_forces_protocol_type_for_unexpected_non_error_status(self):
        service = CodeBuddyStreamService()

        with self.assertRaises(UpstreamAPIError) as raised:
            service._handle_api_error(
                302,
                "upstream supplied error",
                error_type="quota_error",
                code="quota",
            )

        self.assertEqual(raised.exception.error, {
            "message": "CodeBuddy API unexpected status: 302",
            "type": "upstream_protocol_error",
        })

    def test_upstream_sse_error_uses_safe_fallbacks_for_unrecognized_shapes(self):
        string_error = CodeBuddyStreamService._upstream_sse_error({"error": "plain failure"})
        invalid_object_error = CodeBuddyStreamService._upstream_sse_error({
            "error": {"message": 1, "type": None},
        })
        invalid_value_error = CodeBuddyStreamService._upstream_sse_error({"error": []})

        self.assertEqual(string_error.error["message"], "plain failure")
        self.assertEqual(invalid_object_error.error, {
            "message": "CodeBuddy upstream stream error",
            "type": "upstream_error",
        })
        self.assertEqual(invalid_value_error.error, invalid_object_error.error)

    async def test_non_stream_response_maps_transport_errors_and_propagates_unexpected_errors(self):
        errors = [
            (httpx.TimeoutException("timeout"), 504),
            (httpx.NetworkError("offline"), 502),
            (httpx.RemoteProtocolError("incomplete response"), 502),
        ]

        for error, expected_status in errors:
            with self.subTest(error=error):
                async def failed_factory(current_error=error):
                    raise current_error

                service = CodeBuddyStreamService(http_client_factory=failed_factory)
                with self.assertRaises(HTTPException) as raised:
                    await service.handle_non_stream_response({}, {})

                self.assertEqual(raised.exception.status_code, expected_status)

        async def failed_factory():
            raise RuntimeError("broken")

        with self.assertRaisesRegex(RuntimeError, "broken"):
            await CodeBuddyStreamService(
                http_client_factory=failed_factory,
            ).handle_non_stream_response({}, {})

    async def test_non_stream_response_reads_upstream_through_stream_context(self):
        client = mock.Mock()
        client.stream.return_value = FakeHttpClient([
            'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}]}\n',
            "data: [DONE]\n",
        ]).stream("POST", "https://unused")
        client.post = mock.AsyncMock(side_effect=AssertionError("must not buffer the upstream response"))

        async def factory():
            return client

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_non_stream_response({}, {})

        self.assertEqual(response["choices"][0]["message"]["content"], "hello")
        client.stream.assert_called_once()
        client.post.assert_not_awaited()

    async def test_non_stream_response_processes_sse_before_upstream_eof(self):
        class PausedByteStream(httpx.AsyncByteStream):
            def __init__(self):
                self.first_chunk_sent = asyncio.Event()
                self.release_tail = asyncio.Event()

            async def __aiter__(self):
                self.first_chunk_sent.set()
                yield b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
                await self.release_tail.wait()
                yield b'data: [DONE]\n\n'

        upstream = PausedByteStream()
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=upstream))
        processed = asyncio.Event()
        original_process_event = StreamResponseAggregator.process_event

        def observe_process_event(aggregator, event):
            original_process_event(aggregator, event)
            processed.set()

        async with httpx.AsyncClient(transport=transport) as client:
            async def factory():
                return client

            with mock.patch.object(StreamResponseAggregator, "process_event", observe_process_event):
                task = asyncio.create_task(
                    CodeBuddyStreamService(http_client_factory=factory).handle_non_stream_response({}, {})
                )
                await upstream.first_chunk_sent.wait()
                await asyncio.wait_for(processed.wait(), 1.0)
                upstream.release_tail.set()
                response = await task

        self.assertEqual(response["choices"][0]["message"]["content"], "hello")

    async def test_error_body_read_failure_preserves_upstream_status_and_closes_response(self):
        cases = [
            (401, 401),
            (429, 429),
            (500, 502),
        ]

        for streaming in (True, False):
            for upstream_status, expected_status in cases:
                with self.subTest(streaming=streaming, upstream_status=upstream_status):
                    class ErrorResponse:
                        status_code = upstream_status

                        def __init__(self):
                            self.exit_count = 0

                        async def __aenter__(self):
                            return self

                        async def __aexit__(self, _exc_type, _exc, _tb):
                            self.exit_count += 1
                            return False

                        async def aread(self):
                            raise httpx.ReadTimeout("error body stalled")

                    upstream = ErrorResponse()
                    client = mock.Mock()
                    client.stream.return_value = upstream

                    async def factory():
                        return client

                    service = CodeBuddyStreamService(http_client_factory=factory)
                    service.connection_manager.retry_delay = 0

                    with self.assertRaises(HTTPException) as raised:
                        if streaming:
                            await self._prefetch_stream(service, {})
                        else:
                            await service.handle_non_stream_response({}, {})

                    self.assertEqual(raised.exception.status_code, expected_status)
                    self.assertEqual(client.stream.call_count, 1)
                    self.assertEqual(upstream.exit_count, 1)

    async def test_upstream_http_error_preserves_safe_error_fields_and_retry_header(self):
        async def factory():
            return FakeHttpClient(
                [],
                status_code=429,
                text='{"error":{"message":"quota exhausted","type":"quota_error","code":"quota"}}',
                headers={"Retry-After": "7", "WWW-Authenticate": "Bearer realm=upstream"},
            )

        with self.assertRaises(UpstreamAPIError) as raised:
            await CodeBuddyStreamService(http_client_factory=factory).handle_non_stream_response({}, {})

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.error, {
            "message": "quota exhausted",
            "type": "quota_error",
            "code": "quota",
        })
        self.assertEqual(raised.exception.headers, {"Retry-After": "7"})

    async def test_upstream_401_does_not_forward_bearer_challenge(self):
        async def factory():
            return FakeHttpClient(
                [],
                status_code=401,
                text='{"error":{"message":"expired credential","type":"authentication_error"}}',
                headers={"WWW-Authenticate": "Bearer realm=upstream"},
            )

        with self.assertRaises(UpstreamAPIError) as raised:
            await CodeBuddyStreamService(http_client_factory=factory).handle_non_stream_response({}, {})

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.error["message"], "expired credential")
        self.assertIsNone(raised.exception.headers)

    async def test_stream_response_retries_connect_error_before_first_output(self):
        client = mock.Mock()
        client.stream.side_effect = [
            FakeHttpClient([httpx.ConnectError("offline")]).stream("POST", "https://unused"),
            FakeHttpClient([
                'data: {"choices":[{"delta":{"content":"recovered"},"finish_reason":"stop"}]}\n',
                "data: [DONE]\n",
            ]).stream("POST", "https://unused"),
        ]

        async def factory():
            return client

        service = CodeBuddyStreamService(http_client_factory=factory)
        service.connection_manager.retry_delay = 0
        response = await service.handle_stream_response({"model": "model"}, {})
        body = "".join([part async for part in response.body_iterator])

        self.assertIn('"content": "recovered"', body)
        self.assertNotIn("connection_retry", body)
        self.assertEqual(client.stream.call_count, 2)

    async def test_stream_response_does_not_retry_remote_protocol_error_before_first_output(self):
        client = mock.Mock()
        client.stream.side_effect = [
            FakeHttpClient([
                httpx.RemoteProtocolError("incomplete chunked response")
            ]).stream("POST", "https://unused"),
            FakeHttpClient([]).stream("POST", "https://unused"),
        ]

        async def factory():
            return client

        service = CodeBuddyStreamService(http_client_factory=factory)
        service.connection_manager.retry_delay = 0
        with self.assertRaises(HTTPException) as raised:
            await self._prefetch_stream(service)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(client.stream.call_count, 1)

    async def test_stream_response_does_not_retry_after_first_output(self):
        cases = [
            (httpx.ReadTimeout("read timeout"), "upstream_timeout"),
            (httpx.NetworkError("connection lost"), "upstream_transport_error"),
            (httpx.RemoteProtocolError("incomplete chunked response"), "upstream_transport_error"),
            (RuntimeError("broken stream"), "stream_error"),
        ]

        for error, error_type in cases:
            with self.subTest(error=error):
                client = mock.Mock()
                client.stream.return_value = FakeHttpClient([
                    'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n',
                    error,
                ]).stream("POST", "https://unused")

                async def factory():
                    return client

                response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
                    {"model": "model"},
                    {},
                )
                body = "".join([part async for part in response.body_iterator])
                payloads = [
                    json.loads(event.removeprefix("data: "))
                    for event in body.split("\n\n")
                    if event.startswith("data: ") and event != "data: [DONE]"
                ]

                self.assertIn('"content": "partial"', body)
                self.assertEqual(payloads[-1]["error"]["type"], error_type)
                self.assertIn(str(error), payloads[-1]["error"]["message"])
                self.assertEqual(client.stream.call_count, 1)

    async def test_stream_response_maps_transport_error_before_first_output(self):
        cases = [
            (httpx.TimeoutException("timeout"), 504),
            (httpx.NetworkError("offline"), 502),
            (httpx.RemoteProtocolError("incomplete response"), 502),
        ]

        for error, expected_status in cases:
            with self.subTest(error=error):
                async def factory(current_error=error):
                    return FakeHttpClient([current_error])

                service = CodeBuddyStreamService(http_client_factory=factory)
                service.connection_manager.max_connect_retries = 0

                with self.assertRaises(HTTPException) as context:
                    await self._prefetch_stream(service)

                self.assertEqual(context.exception.status_code, expected_status)

    async def test_stream_body_produces_first_complete_sse_event_before_eof(self):
        class PausedByteStream(httpx.AsyncByteStream):
            def __init__(self):
                self.first_chunk_sent = asyncio.Event()
                self.release_tail = asyncio.Event()

            async def __aiter__(self):
                self.first_chunk_sent.set()
                yield b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
                await self.release_tail.wait()
                yield b'data: [DONE]\n\n'

        upstream = PausedByteStream()
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=upstream))

        async with httpx.AsyncClient(transport=transport) as client:
            async def factory():
                return client

            response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
                {"model": "model"},
                {},
            )
            task = asyncio.create_task(anext(response.body_iterator))
            await upstream.first_chunk_sent.wait()
            try:
                first_chunk = await asyncio.wait_for(task, 1.0)
            finally:
                upstream.release_tail.set()

            body = first_chunk + "".join([part async for part in response.body_iterator])

        self.assertIn('"content": "hello"', body)

    async def test_stream_response_closes_upstream_when_client_disconnects_before_first_chunk(self):
        class PausedResponse:
            status_code = 200
            headers = {}

            def __init__(self):
                self.read_started = asyncio.Event()
                self.exited = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                self.exited = True
                return False

            async def aiter_text(self, chunk_size=None):
                self.read_started.set()
                await asyncio.Event().wait()
                yield  # pragma: no cover

        upstream = PausedResponse()
        client = mock.Mock()
        client.stream.return_value = upstream

        async def factory():
            return client

        response = await asyncio.wait_for(
            CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
                {"model": "model"},
                {},
            ),
            1.0,
        )
        sent_messages = []

        async def receive():
            await upstream.read_started.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent_messages.append(message)

        await asyncio.wait_for(
            response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            ),
            1.0,
        )

        self.assertTrue(upstream.exited)
        self.assertEqual(sent_messages, [])

    async def test_stream_response_closes_upstream_when_client_disconnects_after_first_chunk(self):
        class OpenResponse:
            status_code = 200
            headers = {}

            def __init__(self):
                self.exited = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                self.exited = True
                return False

            async def aiter_text(self, chunk_size=None):
                yield 'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n'
                await asyncio.Event().wait()

        upstream = OpenResponse()
        client = mock.Mock()
        client.stream.return_value = upstream

        async def factory():
            return client

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
            {"model": "model"},
            {},
        )
        first_body_sent = asyncio.Event()
        sent_messages = []

        async def receive():
            await first_body_sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent_messages.append(message)
            if message["type"] == "http.response.body" and message["body"]:
                first_body_sent.set()

        await asyncio.wait_for(
            response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            ),
            1.0,
        )

        self.assertTrue(upstream.exited)
        self.assertEqual(sent_messages[0]["type"], "http.response.start")

    async def test_stream_response_propagates_upstream_error_before_response_start(self):
        async def factory():
            return FakeHttpClient(
                [],
                status_code=429,
                text='{"error":{"message":"quota exhausted","type":"quota_error"}}',
            )

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
            {"model": "model"},
            {},
        )
        sent_messages = []

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            sent_messages.append(message)

        with self.assertRaises(UpstreamAPIError) as raised:
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(sent_messages, [])

    async def test_stream_response_upstream_error_reaches_http_exception_handler(self):
        async def factory():
            return FakeHttpClient(
                [],
                status_code=429,
                text='{"error":{"message":"quota exhausted","type":"quota_error"}}',
            )

        service = CodeBuddyStreamService(http_client_factory=factory)
        app = FastAPI()

        @app.exception_handler(UpstreamAPIError)
        async def handle_upstream_error(_request, error):
            return JSONResponse(status_code=error.status_code, content={"error": error.error})

        @app.get("/")
        async def stream_endpoint():
            return await service.handle_stream_response({"model": "model"}, {})

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await client.get("/")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["message"], "quota exhausted")

    async def test_stream_response_sends_complete_asgi_response(self):
        async def factory():
            return FakeHttpClient([
                'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}]}\n',
                "data: [DONE]\n",
            ])

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
            {"model": "model"},
            {},
        )
        sent_messages = []

        async def receive():
            await asyncio.Event().wait()

        async def send(message):
            sent_messages.append(message)

        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

        body = b"".join(
            message["body"]
            for message in sent_messages
            if message["type"] == "http.response.body"
        ).decode("utf-8")
        self.assertEqual(sent_messages[0]["type"], "http.response.start")
        self.assertIs(sent_messages[-1]["more_body"], False)
        self.assertIn('"content": "hello"', body)
        self.assertIn("data: [DONE]", body)

    async def test_stream_response_closes_upstream_when_response_start_fails(self):
        class OpenResponse:
            status_code = 200

            def __init__(self):
                self.exited = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                self.exited = True
                return False

            async def aiter_text(self, chunk_size=None):
                yield 'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n'
                await asyncio.Event().wait()

        upstream = OpenResponse()
        client = mock.Mock()
        client.stream.return_value = upstream

        async def factory():
            return client

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
            {"model": "model"},
            {},
        )

        async def receive():
            await asyncio.Event().wait()

        async def send(_message):
            raise OSError("client disconnected")

        with self.assertRaises(ClientDisconnect):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            )

        self.assertTrue(upstream.exited)

    async def test_stream_response_enforces_total_first_chunk_deadline(self):
        async def factory():
            await asyncio.Event().wait()

        service = CodeBuddyStreamService(http_client_factory=factory, first_chunk_timeout=0.001)
        with self.assertRaises(HTTPException) as raised:
            await self._prefetch_stream(service)

        self.assertEqual(raised.exception.status_code, 504)

    async def test_non_stream_response_retries_only_connect_stage_failure(self):
        attempts = 0

        async def factory():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("offline")
            return FakeHttpClient([
                'data: {"choices":[{"delta":{"content":"recovered"},"finish_reason":"stop"}]}\n',
                "data: [DONE]\n",
            ])

        service = CodeBuddyStreamService(http_client_factory=factory)
        service.connection_manager.retry_delay = 0
        response = await service.handle_non_stream_response({"model": "model"}, {})

        self.assertEqual(response["choices"][0]["message"]["content"], "recovered")
        self.assertEqual(attempts, 2)

    async def test_non_stream_response_ignores_events_without_object_payload(self):
        async def factory():
            return FakeHttpClient([
                "data: []\n",
            ])

        with self.assertRaises(UpstreamAPIError) as raised:
            await CodeBuddyStreamService(http_client_factory=factory).handle_non_stream_response(
                {"model": "model"},
                {},
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.error["type"], "upstream_incomplete")

    async def test_normalized_upstream_events_stop_after_done(self):
        upstream = FakeHttpClient([
            "data: [DONE]\n",
            'data: {"choices":[{"delta":{"content":"ignored"}}]}\n',
        ]).stream("POST", "https://unused")

        events = [
            event
            async for event in CodeBuddyStreamService()._iter_normalized_upstream_events(upstream)
        ]

        self.assertEqual(events, [stream_service.SSE_DONE])

    async def test_stream_response_forwards_events_without_object_payload(self):
        async def factory():
            return FakeHttpClient(["data: []\n"])

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
            {"model": "model"},
            {},
        )
        body = "".join([part async for part in response.body_iterator])

        payloads = self._stream_error_payloads(body)

        self.assertTrue(body.startswith("data: []\n\n"))
        self.assertEqual(payloads[-1]["error"]["type"], "upstream_incomplete")
        self.assertNotIn("data: [DONE]", body)

    @staticmethod
    def _stream_error_payloads(body):
        return [
            json.loads(event.removeprefix("data: "))
            for event in body.split("\n\n")
            if event.startswith("data: {")
        ]

    async def test_stream_response_reports_invalid_sse_json_before_output(self):
        async def factory():
            return FakeHttpClient(["data: not-json\n"])

        with self.assertRaises(UpstreamAPIError) as raised:
            await self._prefetch_stream(CodeBuddyStreamService(http_client_factory=factory))

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.error["type"], "upstream_protocol_error")

    async def test_non_stream_response_reports_invalid_sse_json(self):
        async def factory():
            return FakeHttpClient(["data: not-json\n"])

        with self.assertRaises(UpstreamAPIError) as raised:
            await CodeBuddyStreamService(http_client_factory=factory).handle_non_stream_response(
                {"model": "model"},
                {},
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.error["type"], "upstream_protocol_error")

    async def test_stream_response_reports_incomplete_eof_before_output(self):
        async def factory():
            return FakeHttpClient([])

        with self.assertRaises(UpstreamAPIError) as raised:
            await self._prefetch_stream(CodeBuddyStreamService(http_client_factory=factory))

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.error["type"], "upstream_incomplete")

    async def test_stream_response_reports_invalid_sse_json_after_output(self):
        async def factory():
            return FakeHttpClient([
                'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n',
                "data: not-json\n",
            ])

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
            {"model": "model"},
            {},
        )
        body = "".join([part async for part in response.body_iterator])
        payloads = self._stream_error_payloads(body)

        self.assertEqual(payloads[-1]["error"]["type"], "upstream_protocol_error")
        self.assertNotIn("data: [DONE]", body)

    async def test_stream_response_reports_explicit_upstream_sse_error_before_output(self):
        async def factory():
            return FakeHttpClient([
                'data: {"error":{"message":"quota exhausted","type":"quota_error"}}\n',
                "data: [DONE]\n",
            ])

        with self.assertRaises(UpstreamAPIError) as raised:
            await self._prefetch_stream(CodeBuddyStreamService(http_client_factory=factory))

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.error["message"], "quota exhausted")
        self.assertEqual(raised.exception.error["type"], "quota_error")

    async def test_stream_response_preserves_upstream_sse_error_code_after_output(self):
        async def factory():
            return FakeHttpClient([
                'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n',
                'data: {"error":{"message":"quota exhausted","type":"quota_error","code":"quota"}}\n',
            ])

        response = await CodeBuddyStreamService(http_client_factory=factory).handle_stream_response(
            {"model": "model"},
            {},
        )
        body = "".join([part async for part in response.body_iterator])
        payloads = self._stream_error_payloads(body)

        self.assertEqual(payloads[-1]["error"], {
            "message": "quota exhausted",
            "type": "quota_error",
            "code": "quota",
        })

    async def test_non_stream_response_reports_explicit_upstream_sse_error(self):
        async def factory():
            return FakeHttpClient([
                'data: {"error":{"message":"quota exhausted","type":"quota_error"}}\n',
                "data: [DONE]\n",
            ])

        with self.assertRaises(UpstreamAPIError) as raised:
            await CodeBuddyStreamService(http_client_factory=factory).handle_non_stream_response(
                {"model": "model"},
                {},
            )

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.error["message"], "quota exhausted")


class StreamingFormatTests(unittest.IsolatedAsyncioTestCase):
    async def _render_stream_body(self, chunks, model="glm-5.1", status_code=200, text=""):
        async def fake_get_http_client():
            return FakeHttpClient(chunks, status_code=status_code, text=text)

        response = await CodeBuddyStreamService(http_client_factory=fake_get_http_client).handle_stream_response(
            {"model": model},
            {},
            response_model=model,
        )
        body_parts = []
        async for part in response.body_iterator:
            body_parts.append(part.decode("utf-8") if isinstance(part, bytes) else part)

        return "".join(body_parts)

    async def _render_non_stream_response(self, chunks, model="glm-5.1", status_code=200, text=""):
        async def fake_get_http_client():
            return FakeHttpClient(chunks, status_code=status_code, text=text)

        return await CodeBuddyStreamService(http_client_factory=fake_get_http_client).handle_non_stream_response(
            {"model": model},
            {},
            response_model=model,
        )

    def _stream_payloads(self, body):
        events = [event for event in body.split("\n\n") if event]
        return [
            json.loads(event[6:])
            for event in events
            if event.startswith("data: ") and event != "data: [DONE]"
        ]

    async def test_stream_response_uses_openai_sse_event_boundaries(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        events = [event for event in body.split("\n\n") if event]

        self.assertEqual(len(events), 3)
        self.assertEqual(events[2], "data: [DONE]")
        self.assertTrue(events[0].startswith("data: "))

        payload = json.loads(events[0][6:])
        self.assertEqual(payload["object"], "chat.completion.chunk")
        self.assertEqual(payload["model"], "glm-5.1")
        self.assertIn("id", payload)
        self.assertIn("created", payload)
        self.assertEqual(payload["choices"][0]["delta"], {"role": "assistant"})

        content_payload = json.loads(events[1][6:])
        self.assertEqual(content_payload["id"], payload["id"])
        self.assertEqual(content_payload["created"], payload["created"])
        self.assertEqual(content_payload["choices"][0]["delta"]["content"], "hi")

    async def test_stream_response_adds_done_after_explicit_finish_reason_at_eof(self):
        body = await self._render_stream_body([
            'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":"stop"}]}\n',
        ])

        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    async def test_stream_and_non_stream_use_client_facing_response_model(self):
        chunks = [
            'data: {"id":"upstream","model":"glm-5.2","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":"stop"}]}\n',
            "data: [DONE]\n",
        ]

        stream_body = await self._render_stream_body(chunks, model="codebuddy/glm-5.2")
        non_stream = await self._render_non_stream_response(chunks, model="codebuddy/glm-5.2")

        self.assertEqual(
            {payload["model"] for payload in self._stream_payloads(stream_body)},
            {"codebuddy/glm-5.2"},
        )
        self.assertEqual(non_stream["model"], "codebuddy/glm-5.2")
        self.assertTrue(non_stream["id"].startswith("chatcmpl-"))

    async def test_stream_response_normalizes_reasoning_chunks_for_opencode(self):
        chunks = [
            'data: {"id":"upstream-1","created":1,"model":"wrong","choices":[{"index":0,"delta":{"role":"assistant","reasoning_content":"我","content":"","function_call":null,"refusal":null,"tool_calls":[],"extra_fields":{}},"finish_reason":null}]}\n'
            'data: {"id":"upstream-2","created":2,"model":"wrong","choices":[{"index":0,"delta":{"role":"","reasoning_content":"需要","content":null},"finish_reason":null}]}\n'
            'data: {"id":"upstream-3","created":3,"model":"wrong","choices":[{"index":0,"delta":{"role":"","reasoning_content":"先","content":""},"finish_reason":null}]}\n'
            'data: {"id":"upstream-4","created":4,"model":"wrong","choices":[{"index":0,"delta":{"role":"","content":"结论"},"finish_reason":null}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)

        self.assertEqual([payload["choices"][0]["delta"] for payload in payloads], [
            {"role": "assistant"},
            {"reasoning_content": "我"},
            {"reasoning_content": "需要"},
            {"reasoning_content": "先"},
            {"content": "结论"},
        ])
        self.assertEqual(len({payload["id"] for payload in payloads}), 1)
        self.assertEqual(len({payload["created"] for payload in payloads}), 1)
        self.assertEqual({payload["model"] for payload in payloads}, {"glm-5.1"})

    async def test_stream_response_removes_empty_function_call_object(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"content":"结论"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"role":"","function_call":{"name":"","arguments":""},"refusal":"","extra_fields":null},"finish_reason":"stop"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)

        deltas = [payload["choices"][0]["delta"] for payload in payloads]
        self.assertEqual(deltas, [{"role": "assistant"}, {"content": "结论"}, {}])
        self.assertEqual(payloads[2]["choices"][0]["finish_reason"], "stop")

    async def test_stream_response_preserves_content_after_tool_calls(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"先想"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"tooluse_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"content":"继续思考"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)
        deltas = [payload["choices"][0]["delta"] for payload in payloads]

        self.assertEqual(deltas[0], {"role": "assistant"})
        self.assertEqual(deltas[1], {"reasoning_content": "先想"})
        self.assertEqual(deltas[2]["tool_calls"][0]["id"], "tooluse_1")
        self.assertEqual(deltas[3], {"content": "继续思考"})
        self.assertEqual(deltas[4], {})
        self.assertEqual(payloads[4]["choices"][0]["finish_reason"], "tool_calls")

    async def test_stream_response_keeps_content_and_reasoning_after_tool_calls_separate(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"tooluse_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"content":"工具后文本"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"继续推理"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)
        deltas = [payload["choices"][0]["delta"] for payload in payloads]

        self.assertEqual(deltas[0], {"role": "assistant"})
        self.assertEqual(deltas[2], {"content": "工具后文本"})
        self.assertEqual(deltas[3], {"reasoning_content": "继续推理"})

    async def test_stream_response_keeps_reasoning_after_tool_calls(self):
        chunks = [
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"tooluse_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"reasoning_content":"工具后继续想"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        body = await self._render_stream_body(chunks)
        payloads = self._stream_payloads(body)
        deltas = [payload["choices"][0]["delta"] for payload in payloads]

        self.assertEqual(deltas[0], {"role": "assistant"})
        self.assertEqual(deltas[2], {"reasoning_content": "工具后继续想"})
        self.assertFalse(any(delta.get("content") == "工具后继续想" for delta in deltas))

    async def test_stream_response_maps_upstream_api_error_before_returning_response(self):
        with self.assertRaises(HTTPException) as context:
            await self._render_stream_body([], status_code=429, text="too many")

        self.assertEqual(context.exception.status_code, 429)

    async def test_non_stream_response_preserves_reasoning_and_content_after_tool_calls(self):
        chunks = [
            'data: {"id":"chatcmpl-1","model":"glm-5.1","choices":[{"index":0,"delta":{"reasoning_content":"先想"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{}"}}]},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{"content":"继续思考"},"finish_reason":null}]}\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n'
            "data: [DONE]\n"
        ]

        response = await self._render_non_stream_response(chunks)
        message = response["choices"][0]["message"]

        self.assertEqual(message["content"], "继续思考")
        self.assertEqual(message["reasoning_content"], "先想")
        self.assertEqual(message["tool_calls"][0]["id"], "call_1")
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")

    async def test_non_stream_response_maps_upstream_api_error(self):
        with self.assertRaises(HTTPException) as context:
            await self._render_non_stream_response([], status_code=429, text="rate limited")

        self.assertEqual(context.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
