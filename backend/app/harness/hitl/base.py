"""HITLManager — human-in-the-loop approval for pipeline steps."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .._types import HarnessEvent
from ..events import EventBus
from ._types import HITLCheckpoint, HITLDecision, HITLState
from .store import HITLStore


class HITLManager:
    """Manages interrupt-resume cycles for pipeline steps requiring human approval.

    Usage:
        1. ``interrupt(step_name, state)`` pauses the pipeline
        2. External system (API/UI) calls ``decide(hitl_id, decision)``
        3. Pipeline calls ``wait_for_decision(hitl_id)`` which resolves when decided
    """

    def __init__(
        self,
        event_bus: EventBus,
        store: HITLStore | None = None,
        checkpoints: list[str] | None = None,
        poll_interval: float = 2.0,
    ) -> None:
        self.event_bus = event_bus
        self.store = store or HITLStore()
        self.checkpoints: dict[str, HITLCheckpoint] = {}
        self.poll_interval = poll_interval
        self._waiters: dict[str, asyncio.Event] = {}

        for name in (checkpoints or []):
            self.checkpoints[name] = HITLCheckpoint(step_name=name)

    def has_checkpoint(self, step_name: str) -> bool:
        return step_name in self.checkpoints

    async def interrupt(self, step_name: str, state: dict[str, Any]) -> HITLState:
        """Pause pipeline and return a HITLState awaiting human decision."""
        hitl_state = HITLState(
            id=HITLState.__module__,  # placeholder
            step_name=step_name,
            pipeline_state=dict(state),
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        from uuid import uuid4
        hitl_state.id = uuid4().hex
        self.store.save(hitl_state)
        self._waiters[hitl_state.id] = asyncio.Event()
        self.event_bus.emit(
            HarnessEvent(
                layer="hitl",
                component=step_name,
                action="interrupted",
                payload={"hitl_id": hitl_state.id},
            )
        )
        return hitl_state

    async def wait_for_decision(self, hitl_id: str, timeout: float = 3600.0) -> HITLDecision:
        """Block until a human decision is made for the given HITL state."""
        evt = self._waiters.get(hitl_id)
        if evt is None:
            raise ValueError(f"Unknown HITL id: {hitl_id}")

        await asyncio.wait_for(evt.wait(), timeout=timeout)

        hitl_state = self.store.load(hitl_id)
        if hitl_state is None:
            raise ValueError(f"HITL state not found: {hitl_id}")

        return HITLDecision(
            action=hitl_state.status,  # type: ignore[arg-type]
            feedback=hitl_state.feedback,
            edited_state=hitl_state.edited_state,
        )

    async def decide(self, hitl_id: str, decision: HITLDecision) -> None:
        """Submit a human decision (called from API endpoint)."""
        hitl_state = self.store.load(hitl_id)
        if hitl_state is None:
            raise ValueError(f"HITL state not found: {hitl_id}")

        hitl_state.status = decision.action
        hitl_state.feedback = decision.feedback
        hitl_state.edited_state = decision.edited_state
        hitl_state.resolved_at = datetime.now(timezone.utc).isoformat()
        self.store.save(hitl_state)

        self.event_bus.emit(
            HarnessEvent(
                layer="hitl",
                component=hitl_state.step_name,
                action=decision.action,
                payload={"hitl_id": hitl_id, "feedback": decision.feedback},
            )
        )

        evt = self._waiters.get(hitl_id)
        if evt:
            evt.set()

    def list_pending(self) -> list[HITLState]:
        return self.store.list_by_status("pending")

    def get(self, hitl_id: str) -> HITLState | None:
        return self.store.load(hitl_id)
