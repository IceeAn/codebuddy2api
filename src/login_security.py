"""管理台登录的频率与昂贵密码校验并发保护。"""

import hashlib
import ipaddress
import math
import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

from config import (
    get_login_global_max_attempts,
    get_login_ip_max_attempts,
    get_login_max_concurrency,
    get_login_rate_window_seconds,
    get_login_username_max_attempts,
)


class LoginLimitError(RuntimeError):
    """登录尝试超过滑动窗口限制。"""

    def __init__(self, retry_after: int):
        super().__init__("Login attempt limit exceeded")
        self.retry_after = max(1, int(retry_after))


class LoginAttemptGuard:
    """维护独立的全局、IP、用户名速率桶及非排队并发闸门。"""

    def __init__(
        self,
        *,
        global_max_attempts: int,
        ip_max_attempts: int,
        username_max_attempts: int,
        window_seconds: int,
        max_concurrency: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.global_max_attempts = global_max_attempts
        self.ip_max_attempts = ip_max_attempts
        self.username_max_attempts = username_max_attempts
        self.window_seconds = window_seconds
        self.max_concurrency = max_concurrency
        self._clock = clock
        self._global_attempts: Deque[float] = deque()
        self._ip_attempts: Dict[str, Deque[float]] = {}
        self._username_attempts: Dict[bytes, Deque[float]] = {}
        self._active = 0
        self._lock = threading.RLock()

    @staticmethod
    def _ip_key(client_ip: Optional[str]) -> str:
        if not client_ip:
            return "<unknown>"
        value = str(client_ip)
        try:
            return ipaddress.ip_address(value).compressed
        except ValueError:
            return value

    @staticmethod
    def _username_key(username: str) -> bytes:
        return hashlib.sha256(username.encode("utf-8")).digest()

    @staticmethod
    def _prune_bucket(bucket: Deque[float], cutoff: float) -> None:
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def _prune_keyed_buckets(self, buckets: Dict, cutoff: float) -> None:
        for key, bucket in list(buckets.items()):
            self._prune_bucket(bucket, cutoff)
            if not bucket:
                del buckets[key]

    @staticmethod
    def _retry_after(bucket: Deque[float], limit: int, now: float, window: int) -> int:
        if len(bucket) < limit:
            return 0
        return max(1, math.ceil(bucket[0] + window - now))

    def record_attempt(self, client_ip: Optional[str], username: str) -> None:
        """原子检查三个速率桶；通过时才登记本次尝试。"""
        now = self._clock()
        cutoff = now - self.window_seconds
        ip_key = self._ip_key(client_ip)
        username_key = self._username_key(username)

        with self._lock:
            self._prune_bucket(self._global_attempts, cutoff)
            self._prune_keyed_buckets(self._ip_attempts, cutoff)
            self._prune_keyed_buckets(self._username_attempts, cutoff)
            ip_bucket = self._ip_attempts.setdefault(ip_key, deque())
            username_bucket = self._username_attempts.setdefault(username_key, deque())
            retry_after = max(
                self._retry_after(
                    self._global_attempts,
                    self.global_max_attempts,
                    now,
                    self.window_seconds,
                ),
                self._retry_after(
                    ip_bucket,
                    self.ip_max_attempts,
                    now,
                    self.window_seconds,
                ),
                self._retry_after(
                    username_bucket,
                    self.username_max_attempts,
                    now,
                    self.window_seconds,
                ),
            )
            if retry_after:
                if not ip_bucket:
                    del self._ip_attempts[ip_key]
                if not username_bucket:
                    del self._username_attempts[username_key]
                raise LoginLimitError(retry_after)

            self._global_attempts.append(now)
            ip_bucket.append(now)
            username_bucket.append(now)

    def try_acquire(self) -> bool:
        """仅在仍有容量时占用名额，不等待也不建立排队任务。"""
        with self._lock:
            if self._active >= self.max_concurrency:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("Login concurrency release without matching acquire")
            self._active -= 1

    def reset(self) -> None:
        """清空进程内状态，仅供生命周期重建与测试隔离使用。"""
        with self._lock:
            self._global_attempts.clear()
            self._ip_attempts.clear()
            self._username_attempts.clear()
            self._active = 0

    @property
    def active_ip_keys(self) -> int:
        return len(self._ip_attempts)

    @property
    def active_username_keys(self) -> int:
        return len(self._username_attempts)

    @property
    def username_keys(self) -> tuple[bytes, ...]:
        return tuple(self._username_attempts)


login_attempt_guard = LoginAttemptGuard(
    global_max_attempts=get_login_global_max_attempts(),
    ip_max_attempts=get_login_ip_max_attempts(),
    username_max_attempts=get_login_username_max_attempts(),
    window_seconds=get_login_rate_window_seconds(),
    max_concurrency=get_login_max_concurrency(),
)
