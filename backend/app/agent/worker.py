"""Background agent worker — consumes request queue and drives AgentLoop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from .bootstrap import get_runtime
from .loop import TurnConfig
from .schemas import AuthContext

logger = logging.getLogger(__name__)


class AgentWorker:
    """Consume USER_MESSAGE requests from StreamBus and run AgentLoop."""

    def __init__(self) -> None:
        self._running = False
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop_task: asyncio.Task[Any] | None = None

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    def _track_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run_forever(self) -> None:
        runtime = get_runtime()
        self._running = True
        self._loop_task = asyncio.current_task()
        logger.info("AgentWorker started")
        try:
            async for session_id, payload in runtime.stream_bus.consume_requests():
                if not self._running:
                    break
                task = asyncio.create_task(self._handle_safe(session_id, payload))
                self._track_task(task)
        finally:
            self._loop_task = None
            logger.info("AgentWorker loop exited")

    async def _handle_safe(self, session_id: str, payload: dict[str, Any]) -> None:
        try:
            await self._handle(session_id, payload)
        except Exception:
            logger.exception("AgentWorker request failed session=%s", session_id)

    async def _handle(self, session_id: str, payload: dict[str, Any]) -> None:
        lock = self._lock_for(session_id)
        async with lock:
            runtime = get_runtime()
            auth = AuthContext(
                user_id=int(payload.get("user_id") or 0),
                tenant_id=str(payload.get("tenant_id") or "default"),
                paper_id=payload.get("paper_id"),
            )
            user_settings = payload.get("user_settings")
            turn = TurnConfig(
                system_prompt=payload.get("system_prompt"),
                tool_mask=set(payload["tool_mask"]) if payload.get("tool_mask") else None,
                max_iterations=int(payload.get("max_iterations") or 12),
                deep_search=bool(payload.get("deep_search")),
                user_settings=user_settings,
            )
            message = payload.get("message") or ""
            run_id = payload.get("run_id")
            if payload.get("streaming"):
                await runtime.loop.run_streaming(
                    session_id,
                    message,
                    auth,
                    turn_config=turn,
                    run_id=run_id,
                )
            else:
                await runtime.loop.run(
                    session_id,
                    message,
                    auth,
                    turn_config=turn,
                    run_id=run_id,
                )

    async def stop(self) -> None:
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.clear()
        logger.info("AgentWorker stopped")


async def start_embedded_worker() -> AgentWorker:
    """Start worker as background task inside API process (dev/single-node)."""
    worker = AgentWorker()
    worker._loop_task = asyncio.create_task(worker.run_forever())
    return worker
