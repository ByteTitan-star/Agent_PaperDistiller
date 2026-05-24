"""HITL middleware — integrates HITL checkpoints into pipeline steps."""

from __future__ import annotations

from typing import Any

from .._types import HarnessEvent
from ..events import EventBus
from .base import HITLDecision, HITLManager


class HITLMiddleware:
    """Checks HITL checkpoints before pipeline step execution.

    Usage in PipelineStep:
        if hitl_middleware.has_checkpoint(step_name):
            decision = await hitl_middleware.check(step_name, state)
            if decision.action == "rejected":
                raise PipelineAborted(...)
            if decision.action == "edited":
                state.update(decision.edited_state)
    """

    def __init__(self, hitl_manager: HITLManager) -> None:
        self.hitl_manager = hitl_manager

    def has_checkpoint(self, step_name: str) -> bool:
        return self.hitl_manager.has_checkpoint(step_name)

    async def check(self, step_name: str, state: dict[str, Any]) -> HITLDecision:
        """Interrupt pipeline if checkpoint exists, wait for human decision."""
        hitl_state = await self.hitl_manager.interrupt(step_name, state)
        decision = await self.hitl_manager.wait_for_decision(hitl_state.id)
        return decision
