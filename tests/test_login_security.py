import unittest

from src.login_security import LoginAttemptGuard, LoginLimitError


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class LoginAttemptGuardTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def _guard(self, **overrides) -> LoginAttemptGuard:
        options = {
            "global_max_attempts": 10,
            "ip_max_attempts": 10,
            "username_max_attempts": 10,
            "window_seconds": 60,
            "max_concurrency": 2,
            "clock": self.clock,
        }
        options.update(overrides)
        return LoginAttemptGuard(**options)

    def test_global_limit_covers_rotating_ips_and_usernames(self):
        guard = self._guard(global_max_attempts=2)

        guard.record_attempt("192.0.2.1", "alice")
        guard.record_attempt("192.0.2.2", "bob")

        with self.assertRaises(LoginLimitError) as context:
            guard.record_attempt("192.0.2.3", "carol")

        self.assertEqual(context.exception.retry_after, 60)

    def test_ip_and_username_limits_are_independent(self):
        ip_guard = self._guard(ip_max_attempts=2)
        ip_guard.record_attempt("192.0.2.1", "alice")
        ip_guard.record_attempt("192.0.2.1", "bob")
        with self.assertRaises(LoginLimitError):
            ip_guard.record_attempt("192.0.2.1", "carol")

        username_guard = self._guard(username_max_attempts=2)
        username_guard.record_attempt("192.0.2.1", "alice")
        username_guard.record_attempt("192.0.2.2", "alice")
        with self.assertRaises(LoginLimitError):
            username_guard.record_attempt("192.0.2.3", "alice")

    def test_blocked_attempt_does_not_extend_window_and_expiry_prunes_keys(self):
        guard = self._guard(ip_max_attempts=1)
        guard.record_attempt("2001:0db8::1", "alice")
        self.clock.value = 159.1

        with self.assertRaises(LoginLimitError) as context:
            guard.record_attempt("2001:db8::1", "bob")
        self.assertEqual(context.exception.retry_after, 1)

        self.clock.value = 160.0
        guard.record_attempt("2001:db8::1", "bob")
        self.assertEqual(guard.active_ip_keys, 1)
        self.assertEqual(guard.active_username_keys, 1)

    def test_username_keys_are_digests_and_missing_client_uses_stable_bucket(self):
        guard = self._guard(username_max_attempts=1, ip_max_attempts=1)
        guard.record_attempt(None, "sensitive-user")

        self.assertNotIn("sensitive-user", guard.username_keys)
        with self.assertRaises(LoginLimitError):
            guard.record_attempt(None, "different-user")

        hostname_guard = self._guard(ip_max_attempts=1)
        hostname_guard.record_attempt("proxy.internal", "alice")
        with self.assertRaises(LoginLimitError):
            hostname_guard.record_attempt("proxy.internal", "bob")

    def test_concurrency_gate_rejects_without_queueing_and_releases_capacity(self):
        guard = self._guard(max_concurrency=2)

        self.assertTrue(guard.try_acquire())
        self.assertTrue(guard.try_acquire())
        self.assertFalse(guard.try_acquire())

        guard.release()
        self.assertTrue(guard.try_acquire())
        guard.release()
        guard.release()

        with self.assertRaisesRegex(RuntimeError, "without matching acquire"):
            guard.release()

    def test_reset_clears_attempts_and_concurrency(self):
        guard = self._guard(max_concurrency=1)
        guard.record_attempt("192.0.2.1", "alice")
        self.assertTrue(guard.try_acquire())

        guard.reset()

        self.assertEqual(guard.active_ip_keys, 0)
        self.assertEqual(guard.active_username_keys, 0)
        self.assertTrue(guard.try_acquire())


if __name__ == "__main__":
    unittest.main()
