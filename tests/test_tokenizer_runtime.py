"""隔离进程的容量、退出、超时及缓存生命周期。"""

import tests  # 在生产模块导入前隔离测试数据目录。

from collections import OrderedDict
import unittest
import multiprocessing
from unittest import mock

import config
from src.tokenizer_engine import TokenizerError
from src.tokenizer_runtime import TokenizerRuntime, perform_task, worker_main
from tests.test_tokenizer import tokenizer_files


class TokenizerRuntimeTests(unittest.TestCase):
    def snapshot(self, key="a", template=None):
        return {
            "cache_key": key,
            "files": tokenizer_files(template),
            "profile": {"encoder": "auto"},
        }

    def test_cache_lru_and_validation(self):
        cache = OrderedDict()
        limits = {**config.get_tokenizer_limits(), "cache_size": 1}
        self.assertEqual(
            perform_task(cache, "text", self.snapshot(), "hello", limits), (1, "text")
        )
        engine = cache["a"]
        perform_task(cache, "text", self.snapshot(), "world", limits)
        self.assertIs(cache["a"], engine)
        self.assertEqual(
            perform_task(
                cache,
                "messages",
                self.snapshot("b"),
                {"messages": [{"role": "user", "content": "hello"}]},
                limits,
            )[1],
            "budget_v1",
        )
        self.assertEqual(list(cache), ["b"])
        self.assertEqual(
            perform_task(cache, "validate", self.snapshot(), None)["format"], "hf"
        )

    def test_visualization_uses_the_same_cached_engine_as_counting(self):
        cache = OrderedDict()
        snapshot = self.snapshot()
        perform_task(cache, "text", snapshot, "hello")
        engine = cache["a"]
        snapshot["files"] = {}
        result, method = perform_task(cache, "encode", snapshot, "hello  你好")
        self.assertIs(cache["a"], engine)
        self.assertEqual(method, "text")
        self.assertEqual(result, {
            "text": "hello  你好", "input_tokens": 2,
            "tokens": [
                {"id": 1, "start": 0, "end": 5},
                {"id": 3, "start": 7, "end": 13},
            ],
        })

    def test_worker_handles_safe_errors_and_closes(self):
        connection = mock.Mock()
        connection.recv.side_effect = [
            ("text", self.snapshot(), "hello"),
            ("text", {}, ""),
            ("text", self.snapshot(), 3),
            EOFError,
        ]
        with mock.patch("src.tokenizer_runtime.sys.platform", "linux"), mock.patch(
            "resource.setrlimit"
        ) as limit:
            worker_main(connection, config.get_tokenizer_limits())
        limit.assert_called_once()
        self.assertEqual(connection.send.call_args_list[0].args[0], (True, (1, "text")))
        self.assertEqual(connection.send.call_args_list[1].args[0][1][1], 500)
        self.assertEqual(connection.send.call_args_list[2].args[0][1][1], 400)
        connection.close.assert_called_once()
        connection.reset_mock()
        connection.recv.side_effect = EOFError
        with mock.patch("src.tokenizer_runtime.sys.platform", "darwin"):
            worker_main(connection, config.get_tokenizer_limits())
        connection.close.assert_called_once()

    def test_real_process_is_reused_and_shutdown_closes_it(self):
        runtime = TokenizerRuntime()
        self.addCleanup(runtime.shutdown)
        self.assertEqual(runtime.execute("text", self.snapshot(), "hello"), (1, "text"))
        worker = runtime.workers[0]
        with self.assertRaises(TokenizerError):
            runtime.execute("text", self.snapshot(), 42)
        self.assertIs(runtime.workers[0], worker)
        self.assertEqual(
            runtime.execute("text", lambda: self.snapshot(), "hello world"), (2, "text")
        )
        runtime.shutdown()
        self.assertFalse(worker[0].is_alive())
        with self.assertRaises(TokenizerError) as caught:
            runtime.execute("text", self.snapshot(), "hello")
        self.assertEqual(caught.exception.status_code, 503)
        runtime.startup()
        self.assertFalse(runtime.closed)

    def test_capacity_and_forced_kill(self):
        runtime = TokenizerRuntime()
        runtime.capacity = mock.Mock()
        runtime.capacity.acquire.return_value = False
        with self.assertRaises(TokenizerError) as caught:
            runtime.execute("text", {}, "")
        self.assertEqual(caught.exception.status_code, 429)
        worker = (mock.Mock(), mock.Mock())
        TokenizerRuntime.stop_worker(worker)
        worker[0].kill.assert_called_once()
        worker[0].is_alive.side_effect = [False, False]
        worker[0].reset_mock()
        TokenizerRuntime.stop_worker(worker)
        worker[0].terminate.assert_not_called()

    def test_spawn_failures_close_both_pipe_ends(self):
        runtime = TokenizerRuntime()
        context = mock.Mock()
        context.Pipe.return_value = (mock.Mock(), mock.Mock())
        context.Process.return_value.start.side_effect = OSError("bad")
        with mock.patch(
            "src.tokenizer_runtime.multiprocessing.get_context", return_value=context
        ):
            with self.assertRaises(TokenizerError):
                runtime.execute("text", {}, "")
        for connection in context.Pipe.return_value:
            connection.close.assert_called_once()
        self.assertEqual(runtime.workers, [])

    def test_memory_timeout_and_broken_transport_discard_workers(self):
        for case in ("memory", "timeout", "transport", "removed"):
            with self.subTest(case=case):
                runtime = TokenizerRuntime(
                    {**config.get_tokenizer_limits(), "timeout_seconds": 0}
                )
                worker = (mock.Mock(pid=123), mock.Mock())
                runtime.workers = [worker]
                runtime.idle = [worker]
                monitor = mock.Mock()
                monitor.memory_info.return_value.rss = 10**12 if case == "memory" else 1
                if case == "transport":
                    worker[1].recv.side_effect = EOFError
                    event = None
                else:
                    event = mock.Mock()
                    event.wait.return_value = False

                def remove():
                    runtime.workers = []
                    return self.snapshot()

                with mock.patch(
                    "src.tokenizer_runtime.psutil.Process", return_value=monitor
                ), mock.patch.object(runtime, "stop_worker") as stop:
                    if event is None:
                        with self.assertRaises(TokenizerError):
                            runtime.execute("text", self.snapshot(), "hello")
                    else:
                        with mock.patch(
                            "src.tokenizer_runtime.threading.Event", return_value=event
                        ), mock.patch("src.tokenizer_runtime.threading.Thread"):
                            with self.assertRaises(TokenizerError) as caught:
                                runtime.execute(
                                    "text",
                                    remove if case == "removed" else self.snapshot(),
                                    "hello",
                                )
                            self.assertIn(
                                "内存" if case == "memory" else "超时",
                                str(caught.exception),
                            )
                    self.assertEqual(stop.call_count, 0 if case == "removed" else 1)
                self.assertEqual(runtime.workers, [])

    def test_runaway_template_is_terminated_and_next_request_recovers(self):
        runtime = TokenizerRuntime(
            {**config.get_tokenizer_limits(), "timeout_seconds": 0.2}
        )
        self.addCleanup(runtime.shutdown)
        template = "{% for a in range(100000) %}{% for b in range(100000) %}{% set x = a + b %}{% endfor %}{% endfor %}"
        with self.assertRaises(TokenizerError) as caught:
            runtime.execute(
                "messages",
                self.snapshot(template=template),
                {"messages": [{"role": "user", "content": "hello"}]},
            )
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(runtime.workers, [])
        runtime.limits["timeout_seconds"] = 5
        self.assertEqual(runtime.execute("text", self.snapshot(), "hello"), (1, "text"))

    def test_failed_snapshot_does_not_spawn_a_worker_and_releases_capacity(self):
        runtime = TokenizerRuntime({**config.get_tokenizer_limits(), "workers": 1})
        self.addCleanup(runtime.shutdown)

        def missing():
            self.assertFalse(runtime.capacity.acquire(blocking=False))
            raise TokenizerError("未知模型", 404)

        with mock.patch(
            "src.tokenizer_runtime.multiprocessing.get_context",
            wraps=multiprocessing.get_context,
        ) as spawn:
            for _ in range(3):
                with self.assertRaises(TokenizerError) as caught:
                    runtime.execute("text", missing, "hello")
                self.assertEqual(caught.exception.status_code, 404)
            spawn.assert_not_called()
        self.assertEqual(runtime.workers, [])
        self.assertTrue(runtime.capacity.acquire(blocking=False))
        runtime.capacity.release()

    def test_failed_snapshot_keeps_existing_worker_and_cached_engine(self):
        runtime = TokenizerRuntime({**config.get_tokenizer_limits(), "workers": 1})
        self.addCleanup(runtime.shutdown)
        self.assertEqual(runtime.execute("text", self.snapshot(), "hello"), (1, "text"))
        worker = runtime.workers[0]
        missing = mock.Mock(side_effect=TokenizerError("未知模型", 404))
        with mock.patch.object(
            runtime, "stop_worker", wraps=runtime.stop_worker
        ) as stop:
            for _ in range(3):
                with self.assertRaises(TokenizerError):
                    runtime.execute("text", missing, "hello")
            stop.assert_not_called()
        self.assertEqual(runtime.idle, [worker])
        self.assertTrue(worker[0].is_alive())
        cached = self.snapshot()
        cached["files"] = {}
        self.assertEqual(runtime.execute("text", cached, "hello world"), (2, "text"))
        self.assertIs(runtime.workers[0], worker)

    def test_shutdown_during_preparation_does_not_allocate_worker(self):
        runtime = TokenizerRuntime()

        def resolve():
            runtime.shutdown()
            return self.snapshot()

        with mock.patch(
            "src.tokenizer_runtime.multiprocessing.get_context",
            wraps=multiprocessing.get_context,
        ) as spawn:
            with self.assertRaises(TokenizerError) as caught:
                runtime.execute("text", resolve, "hello")
            self.assertEqual(caught.exception.status_code, 503)
            spawn.assert_not_called()
