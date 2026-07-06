import json
import unittest

from src.sse import (
    SSE_DONE,
    SSEDataError,
    format_sse_done,
    format_sse_error,
    format_sse_event,
    iter_sse_events,
    parse_sse_event,
)

from tests.helpers import async_chunks


class SSEFormatTests(unittest.TestCase):
    def test_format_sse_event_uses_data_only_event_boundary(self):
        self.assertEqual(format_sse_event({"message": "你好"}), 'data: {"message": "你好"}\n\n')

    def test_format_sse_done(self):
        self.assertEqual(format_sse_done(), "data: [DONE]\n\n")

    def test_format_sse_error(self):
        event = format_sse_error("boom", "api_error")

        payload = json.loads(event.removeprefix("data: ").strip())
        self.assertEqual(payload, {"error": {"message": "boom", "type": "api_error"}})


class SSEParseTests(unittest.TestCase):
    def test_parse_sse_event_partitions_valid_done_and_ignored_lines(self):
        cases = [
            ('data: {"a": 1}', {"a": 1}),
            ("data: [DONE]", SSE_DONE),
            ("", None),
            (": keepalive", None),
            ("event: message", None),
            ("data:", None),
        ]

        for line, expected in cases:
            with self.subTest(line=line):
                result = parse_sse_event(line)
                if expected is SSE_DONE:
                    self.assertIs(result, SSE_DONE)
                else:
                    self.assertEqual(result, expected)

    def test_parse_sse_event_reports_invalid_json_data(self):
        with self.assertRaisesRegex(SSEDataError, "invalid JSON"):
            parse_sse_event("data: not-json")


class SSEIteratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_iter_sse_events_reassembles_fragmented_lines(self):
        events = []

        async for event in iter_sse_events(async_chunks('data: {"a"', ': 1}\n', "data: [DONE]\n")):
            events.append(event)

        self.assertEqual(events[0], {"a": 1})
        self.assertIs(events[1], SSE_DONE)

    async def test_iter_sse_events_flushes_final_line_without_newline(self):
        events = []

        async for event in iter_sse_events(async_chunks('data: {"a": 1}')):
            events.append(event)

        self.assertEqual(events, [{"a": 1}])

    async def test_iter_sse_events_skips_empty_and_non_data_chunks(self):
        events = []

        async for event in iter_sse_events(async_chunks("", "event: ignored\n", 'data: {"ok": true}\n')):
            events.append(event)

        self.assertEqual(events, [{"ok": True}])

    async def test_iter_sse_events_ignores_final_non_data_line(self):
        events = []

        async for event in iter_sse_events(async_chunks("event: ignored")):
            events.append(event)

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
