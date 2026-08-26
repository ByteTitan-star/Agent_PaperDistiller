"""滑动窗口（Sliding Window）限流器 — 限制工具调用频率。

防止某个工具在短时间内被过于频繁地调用（如 web_search 暴力调用烧 Tavily 配额）。
在 window_seconds 时间窗口内最多允许 max_calls 次调用。

（v3.0 修正：原模块注释自称"令牌桶/Token Bucket"，但实现实为滑动窗口计数器。）
"""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    """基于滑动窗口计数的工具调用限流器。

    每个工具独立计数，在指定时间窗口内限制最大调用次数。

    使用方式：
        limiter = RateLimiter(max_calls=60, window_seconds=60)  # 每分钟最多 60 次

        if limiter.allow("web_search"):
            # 执行工具调用
        else:
            # 被限流，拒绝或排队

    Attributes:
        max_calls: 时间窗口内允许的最大调用次数。
        window_seconds: 滑动窗口时间长度（秒）。
        _timestamps: 各工具的调用时间戳列表 {"tool_name": [t1, t2, ...]}。
    """

    def __init__(
        self,
        max_calls: int = 60,  # 时间窗口内最大调用次数
        window_seconds: float = 60.0,  # 时间窗口长度（秒）
    ) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        # 使用 defaultdict 避免手动初始化
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def allow(self, tool_name: str) -> bool:
        """检查某个工具是否允许调用。

        清理过期的时间戳，如果当前窗口内的调用次数未超过限制则允许。

        Args:
            tool_name: 工具名称。

        Returns:
            bool: True 表示允许调用，False 表示被限流。
        """
        now = time.monotonic()
        timestamps = self._timestamps[tool_name]
        cutoff = now - self.window_seconds  # 窗口起始时间

        # 清理窗口外的旧时间戳（滑动窗口）
        self._timestamps[tool_name] = [t for t in timestamps if t > cutoff]

        # 检查是否超过限制
        if len(self._timestamps[tool_name]) >= self.max_calls:
            return False  # 被限流

        # 记录本次调用时间
        self._timestamps[tool_name].append(now)
        return True

    def reset(self, tool_name: str | None = None) -> None:
        """重置限流计数。

        Args:
            tool_name: 指定工具名则只重置该工具，None 则重置所有工具。
        """
        if tool_name:
            self._timestamps.pop(tool_name, None)
        else:
            self._timestamps.clear()
