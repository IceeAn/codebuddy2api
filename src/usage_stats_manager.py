"""
Usage Statistics Manager - Tracks usage stats for models and credentials.
"""
import threading
from collections import defaultdict
from typing import DefaultDict


class UsageStatsManager:
    """按本系统用户名隔离记录模型与凭证调用统计。"""

    _instance = None
    # Use RLock (Re-entrant Lock) to prevent deadlocks when one locked function calls another.
    _lock = threading.RLock()
    model_usage: DefaultDict[str, DefaultDict[str, int]]
    credential_usage: DefaultDict[str, DefaultDict[str, int]]

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(UsageStatsManager, cls).__new__(cls)
                    cls._instance.model_usage = defaultdict(lambda: defaultdict(int))
                    cls._instance.credential_usage = defaultdict(lambda: defaultdict(int))
        return cls._instance

    def record_model_usage(self, username: str, model_name: str):
        """记录指定本系统用户对模型的调用次数。"""
        with self._lock:
            self.model_usage[username][model_name] += 1

    def record_credential_usage(self, username: str, credential_id: str):
        """记录指定本系统用户对凭证的调用次数。"""
        with self._lock:
            self.credential_usage[username][credential_id] += 1

    def get_stats(self, username: str):
        """返回指定本系统用户的当前调用统计。"""
        with self._lock:
            return {
                "model_usage": dict(self.model_usage.get(username, {})),
                "credential_usage": dict(self.credential_usage.get(username, {}))
            }

    def _reset_for_tests(self):
        """清空当前进程内所有用户的调用统计，供测试隔离使用。"""
        with self._lock:
            self.model_usage.clear()
            self.credential_usage.clear()


# Global instance of the stats manager
usage_stats_manager = UsageStatsManager()
