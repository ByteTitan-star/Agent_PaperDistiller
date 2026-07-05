"""In-process HITL await primitive — mirrors bioagent HilWaiterRegistry."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..harness.hitl._types import HITLDecision

logger = logging.getLogger(__name__)

__all__ = ["HitlWaiterDecision", "HitlWaiterRegistry"]


@dataclass(frozen=True, slots=True)
class HitlWaiterDecision:
    hil_id: str
    response_option_id: str
    responder_user_id: str
    feedback: str | None = None
    edited_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_hitl_decision(self) -> HITLDecision:
        action = self.response_option_id
        if action not in ("approved", "rejected", "edited"):
            action = "approved"
        return HITLDecision(
            action=action,  # type: ignore[arg-type]
            feedback=self.feedback,
            edited_state=self.edited_state,
        )


class HitlWaiterRegistry:
    """Per-process futures keyed on hil_id."""

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[HitlWaiterDecision]] = {}

    def register(self, hil_id: str) -> asyncio.Future[HitlWaiterDecision]:
        if hil_id in self._waiters:
            raise RuntimeError(f"HITL waiter for {hil_id!r} already registered.")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[HitlWaiterDecision] = loop.create_future()
        self._waiters[hil_id] = future
        return future

    def resolve(self, decision: HitlWaiterDecision) -> None:
        future = self._waiters.pop(decision.hil_id, None)
        if future is None or future.done():
            return
        future.set_result(decision)

    def cancel(self, hil_id: str) -> None:
        future = self._waiters.pop(hil_id, None)
        if future and not future.done():
            future.cancel()

    async def await_response(self, hil_id: str, *, timeout: float = 3600.0) -> HitlWaiterDecision:
        future = self._waiters.get(hil_id)
        if future is None:
            future = self.register(hil_id)
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
