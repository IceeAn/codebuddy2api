"""有界、可终止的分词工作进程，避免上传模板阻塞主服务。"""

import atexit
from collections import OrderedDict
import multiprocessing
import os
import sys
import threading
import time

import psutil

from .tokenizer_engine import TokenizerEngine, TokenizerError, validate_files


def perform_task(cache, operation, snapshot, value, limits=None):
    from config import get_tokenizer_limits

    limits = limits if limits is not None else get_tokenizer_limits()
    if operation == "validate":
        return validate_files(snapshot["files"], snapshot["profile"], limits)
    key = snapshot["cache_key"]
    engine = cache.pop(key, None)
    if engine is None:
        engine = TokenizerEngine(snapshot["files"], snapshot["profile"], limits)
    cache[key] = engine
    while len(cache) > limits["cache_size"]:
        cache.popitem(last=False)
    if operation == "text":
        return engine.count_text(value), "text"
    if operation == "encode":
        return engine.encode_text(value), "text"
    return engine.count_messages(value)


def worker_main(connection, limits):
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if sys.platform == "linux":
        import resource

        resource.setrlimit(
            resource.RLIMIT_AS, (limits["memory_max_bytes"], limits["memory_max_bytes"])
        )
    cache = OrderedDict()
    try:
        while True:
            try:
                operation, snapshot, value = connection.recv()
            except EOFError:
                break
            try:
                result = (True, perform_task(cache, operation, snapshot, value, limits))
            except TokenizerError as error:
                result = (False, (str(error), error.status_code))
            except Exception:
                result = (False, ("Tokenizer 执行失败，请检查资源和模板", 500))
            connection.send(result)
    finally:
        connection.close()


class TokenizerRuntime:
    def __init__(self, limits=None):
        from config import get_tokenizer_limits

        self.limits = limits if limits is not None else get_tokenizer_limits()
        self.lock = threading.Lock()
        self.capacity = threading.BoundedSemaphore(self.limits["workers"])
        self.workers = []
        self.idle = []
        self.closed = False

    def startup(self):
        from config import get_tokenizer_limits

        self.limits = get_tokenizer_limits()
        self.capacity = threading.BoundedSemaphore(self.limits["workers"])
        self.closed = False

    @staticmethod
    def stop_worker(worker):
        process, connection = worker
        connection.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)

    def shutdown(self):
        with self.lock:
            self.closed = True
            workers, self.workers, self.idle = self.workers, [], []
        for worker in workers:
            self.stop_worker(worker)

    def execute(self, operation, snapshot, value=None):
        if not self.capacity.acquire(timeout=self.limits["queue_timeout_seconds"]):
            raise TokenizerError("计数任务繁忙，请稍后重试", 429)
        worker = None
        healthy = False
        try:
            # 文件快照读取仍受容量约束；准备失败不得启动或驱逐工作进程。
            if callable(snapshot):
                snapshot = snapshot()
            with self.lock:
                if self.closed:
                    raise TokenizerError("计数服务正在关闭", 503)
                if self.idle:
                    worker = self.idle.pop()
                else:
                    context = multiprocessing.get_context("spawn")
                    parent, child = context.Pipe()
                    process = context.Process(
                        target=worker_main, args=(child, self.limits), daemon=True
                    )
                    try:
                        process.start()
                    except BaseException:
                        parent.close()
                        child.close()
                        raise
                    child.close()
                    worker = (process, parent)
                    self.workers.append(worker)
            process, connection = worker
            finished = threading.Event()
            response = []

            def exchange():
                try:
                    connection.send((operation, snapshot, value))
                    response.append(connection.recv())
                except (OSError, EOFError, TypeError) as error:
                    response.append(error)
                finally:
                    finished.set()

            transport = threading.Thread(target=exchange, daemon=True)
            transport.start()
            deadline = time.monotonic() + self.limits["timeout_seconds"]
            monitor = psutil.Process(process.pid)
            while not finished.wait(0.05):
                if monitor.memory_info().rss > self.limits["memory_max_bytes"]:
                    raise TokenizerError("Tokenizer 内存使用超过上限", 503)
                if time.monotonic() >= deadline:
                    raise TokenizerError("Tokenizer 执行超时", 503)
            if isinstance(response[0], Exception):
                raise response[0]
            success, result = response[0]
            healthy = True
            if not success:
                raise TokenizerError(*result)
            return result
        except (OSError, EOFError, TypeError, psutil.Error) as error:
            raise TokenizerError("Tokenizer 工作进程异常终止", 503) from error
        finally:
            if worker is not None:
                with self.lock:
                    if worker in self.workers:
                        if healthy:
                            self.idle.append(worker)
                        else:
                            self.workers.remove(worker)
                            self.stop_worker(worker)
            self.capacity.release()


tokenizer_runtime = TokenizerRuntime()
atexit.register(tokenizer_runtime.shutdown)
