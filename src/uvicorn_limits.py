"""Uvicorn 资源限制的版本适配。"""

from typing import Optional


def to_uvicorn_limit_concurrency(
    configured_limit: Optional[int],
) -> Optional[int]:
    """将用户期望的并发容量转换为 Uvicorn 0.49.0 的拒绝阈值。"""
    if configured_limit is None:
        return None
    if (
        isinstance(configured_limit, bool)
        or not isinstance(configured_limit, int)
        or configured_limit < 1
    ):
        raise ValueError("configured concurrency limit must be a positive integer")
    # Uvicorn 在判断前已将当前连接计入集合，阈值需要设置为 N + 1 才能放行 N 个连接。
    return configured_limit + 1
