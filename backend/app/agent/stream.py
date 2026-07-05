"""Stream bus — in-process pub/sub with optional Redis Streams backend."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any, Protocol

from .schemas import EventType, StreamEvent

logger = logging.getLogger(__name__)


class StreamBus(Protocol):
    async def emit(self, event: StreamEvent) -> None: ...
    def subscribe(self, session_id: str) -> AsyncIterator[StreamEvent]: ...
    async def enqueue_request(self, session_id: str, payload: dict[str, Any]) -> None: ...
    def consume_requests(self) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...


class InMemoryStreamBus:
    """Per-session asyncio queues for API ↔ worker communication within one process."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[StreamEvent | None]]] = defaultdict(list)
        self._request_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._seq: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def emit(self, event: StreamEvent) -> None:
        async with self._lock:
            self._seq[event.session_id] += 1
            seq = self._seq[event.session_id]
        enriched = StreamEvent(
            event_type=event.event_type,
            session_id=event.session_id,
            data=event.data,
            run_id=event.run_id,
            seq=seq,
        )
        for queue in list(self._subscribers.get(event.session_id, [])):
            try:
                queue.put_nowait(enriched)
            except asyncio.QueueFull:
                logger.warning("Stream subscriber queue full session=%s", event.session_id)

    async def subscribe(self, session_id: str) -> AsyncIterator[StreamEvent]:
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue(maxsize=512)
        self._subscribers[session_id].append(queue)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            self._subscribers[session_id] = [q for q in self._subscribers.get(session_id, []) if q is not queue]

    def close_session(self, session_id: str) -> None:
        for queue in self._subscribers.pop(session_id, []):
            queue.put_nowait(None)

    async def enqueue_request(self, session_id: str, payload: dict[str, Any]) -> None:
        await self._request_queue.put((session_id, payload))

    async def consume_requests(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        while True:
            yield await self._request_queue.get()


class RedisStreamBus:
    """Redis Streams bus for multi-process API / worker deployment."""

    def __init__(
        self,
        redis_url: str,
        *,
        events_stream: str = "agent:events",
        requests_stream: str = "agent:requests",
    ) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._events_stream = events_stream
        self._requests_stream = requests_stream
        self._group = "agent-workers"
        self._consumer = f"worker-{id(self)}"
        self._seq: dict[str, int] = defaultdict(int)

    async def _ensure_group(self, stream: str) -> None:
        try:
            await self._redis.xgroup_create(stream, self._group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def emit(self, event: StreamEvent) -> None:
        self._seq[event.session_id] += 1
        payload = {
            "event_type": event.event_type.value,
            "session_id": event.session_id,
            "data": json.dumps(event.data, ensure_ascii=False),
            "run_id": event.run_id or "",
            "seq": str(self._seq[event.session_id]),
        }
        await self._redis.xadd(
            f"{self._events_stream}:{event.session_id}",
            payload,
            maxlen=10_000,
            approximate=True,
        )

    async def subscribe(self, session_id: str) -> AsyncIterator[StreamEvent]:
        stream = f"{self._events_stream}:{session_id}"
        last_id = "0-0"
        while True:
            rows = await self._redis.xread({stream: last_id}, block=5000, count=50)
            if not rows:
                continue
            for _, messages in rows:
                for msg_id, fields in messages:
                    last_id = msg_id
                    yield StreamEvent(
                        event_type=EventType(fields["event_type"]),
                        session_id=fields["session_id"],
                        data=json.loads(fields["data"]),
                        run_id=fields.get("run_id") or None,
                        seq=int(fields.get("seq") or 0),
                    )

    async def enqueue_request(self, session_id: str, payload: dict[str, Any]) -> None:
        await self._redis.xadd(
            self._requests_stream,
            {"session_id": session_id, "payload": json.dumps(payload, ensure_ascii=False)},
        )

    async def consume_requests(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        await self._ensure_group(self._requests_stream)
        while True:
            rows = await self._redis.xreadgroup(
                self._group,
                self._consumer,
                {self._requests_stream: ">"},
                block=5000,
                count=10,
            )
            if not rows:
                continue
            for _, messages in rows:
                for msg_id, fields in messages:
                    yield fields["session_id"], json.loads(fields["payload"])
                    await self._redis.xack(self._requests_stream, self._group, msg_id)


def build_stream_bus(redis_url: str | None) -> StreamBus:
    if redis_url:
        try:
            return RedisStreamBus(redis_url)
        except Exception:
            logger.exception("Redis stream bus unavailable; falling back to in-memory")
    return InMemoryStreamBus()
