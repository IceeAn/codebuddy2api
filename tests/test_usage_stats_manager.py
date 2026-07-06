import unittest

from src.usage_stats_manager import UsageStatsManager


class UsageStatsManagerTests(unittest.TestCase):
    def test_existing_singleton_is_reused(self):
        existing = UsageStatsManager._instance

        self.assertIs(UsageStatsManager(), existing)

    def test_inner_singleton_check_handles_concurrent_initialization(self):
        original_instance = UsageStatsManager._instance
        original_lock = UsageStatsManager._lock
        concurrent_instance = object()

        class ConcurrentLock:
            def __enter__(self):
                UsageStatsManager._instance = concurrent_instance

            def __exit__(self, _exc_type, _exc, _tb):
                return False

        try:
            UsageStatsManager._instance = None
            UsageStatsManager._lock = ConcurrentLock()

            self.assertIs(UsageStatsManager(), concurrent_instance)
        finally:
            UsageStatsManager._instance = original_instance
            UsageStatsManager._lock = original_lock


if __name__ == "__main__":
    unittest.main()
