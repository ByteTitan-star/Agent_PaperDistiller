"""Simple token-bucket rate limiter for tool calls."""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    """Per-tool rate limiter using a token-bucket algorithm."""

    def __init__(
        self,
        max_calls: int = 60,
        window_seconds: float = 60.0,
    ) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def allow(self, tool_name: str) -> bool:
        now = time.monotonic()
        timestamps = self._timestamps[tool_name]
        cutoff = now - self.window_seconds
        self._timestamps[tool_name] = [t for t in timestamps if t > cutoff]
        if len(self._timestamps[tool_name]) >= self.max_calls:
            return False
        self._timestamps[tool_name].append(now)
        return True

    def reset(self, tool_name: str | None = None) -> None:
        if tool_name:
            self._timestamps.pop(tool_name, None)
        else:
            self._timestamps.clear()
