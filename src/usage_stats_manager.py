"""
Usage Statistics Manager - Tracks usage stats for models and credentials.
"""
import threading
from collections import defaultdict


class UsageStatsManager:
    _instance = None
    # Use RLock (Re-entrant Lock) to prevent deadlocks when one locked function calls another.
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(UsageStatsManager, cls).__new__(cls)
                    cls._instance.model_usage = defaultdict(int)
                    cls._instance.credential_usage = defaultdict(int)
        return cls._instance

    def record_model_usage(self, model_name: str):
        """Records the usage of a specific model."""
        with self._lock:
            self.model_usage[model_name] += 1

    def record_credential_usage(self, credential_id: str):
        """Records the usage of a specific credential."""
        with self._lock:
            self.credential_usage[credential_id] += 1

    def get_stats(self):
        """Returns all current usage statistics."""
        with self._lock:
            return {
                "model_usage": dict(self.model_usage),
                "credential_usage": dict(self.credential_usage)
            }


# Global instance of the stats manager
usage_stats_manager = UsageStatsManager()
