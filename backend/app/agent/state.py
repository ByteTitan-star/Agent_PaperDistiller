"""Session cancel flags — in-process with optional Redis backend."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class StateStore(Protocol):
    async def mark_cancelled(self, state_key: str) -> None: ...
    async def clear_cancelled(self, state_key: str) -> None: ...
    async def is_cancelled(self, state_key: str) -> bool: ...


class InMemoryStateStore:
    """Process-local cancel flags. Suitable for single-worker dev; swap for Redis in prod."""

    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._lock = asyncio.Lock()

    async def mark_cancelled(self, state_key: str) -> None:
        async with self._lock:
            self._cancelled.add(state_key)

    async def clear_cancelled(self, state_key: str) -> None:
        async with self._lock:
            self._cancelled.discard(state_key)

    async def is_cancelled(self, state_key: str) -> bool:
        async with self._lock:
            return state_key in self._cancelled


class RedisStateStore:
    """Distributed cancel flags when ``redis_url`` is configured."""

    def __init__(self, redis_url: str, *, prefix: str = "agent:cancel:") -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix

    def _key(self, state_key: str) -> str:
        return f"{self._prefix}{state_key}"

    async def mark_cancelled(self, state_key: str) -> None:
        await self._redis.set(self._key(state_key), "1", ex=86400)

    async def clear_cancelled(self, state_key: str) -> None:
        await self._redis.delete(self._key(state_key))

    async def is_cancelled(self, state_key: str) -> bool:
        return bool(await self._redis.exists(self._key(state_key)))


def build_state_store(redis_url: str | None) -> StateStore:
    if redis_url:
        try:
            return RedisStateStore(redis_url)
        except Exception:
            logger.exception("Redis state store unavailable; falling back to in-memory")
    return InMemoryStateStore()
