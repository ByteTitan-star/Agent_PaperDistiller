"""Unified HITL coordinator — StreamBus + store + waiter registry (bioagent P1)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..agent.hitl import HitlWaiterDecision, HitlWaiterRegistry
from ..agent.schemas import EventType, StreamEvent
from ..harness.hitl._types import HITLDecision, HITLState
from ..harness.hitl.store import HITLStore
from .hitl_persistence import persist_hitl_part
from .hitl_sse import build_hitl_request_envelope, hitl_request_to_sse

logger = logging.getLogger(__name__)

_coordinator: HitlCoordinator | None = None


class HitlCoordinator:
    """Emit HITL_REQUEST on StreamBus; resolve via HITL_RESPONSE + waiter registry."""

    def __init__(
        self,
        store: HITLStore,
        stream_bus_getter: Callable[[], Any],
    ) -> None:
        self.store = store
        self._stream_bus_getter = stream_bus_getter
        self.waiters = HitlWaiterRegistry()

    def _bus(self):
        return self._stream_bus_getter()

    async def request(
        self,
        *,
        session_id: str,
        user_id: int | None,
        paper_id: str | None,
        hil_event_type: str,
        payload: dict[str, Any],
        streaming: bool = False,
        title: str = "",
        message: str = "",
        options: list[dict[str, str]] | None = None,
    ) -> tuple[str, str]:
        """Persist, emit HITL_REQUEST, return (hil_id, sse_event)."""
        hil_id = uuid4().hex
        full_payload = {
            **payload,
            "session_id": session_id,
            "user_id": user_id,
            "paper_id": paper_id,
            "streaming": streaming,
            "title": title,
            "message": message,
        }
        hitl_state = HITLState(
            id=hil_id,
            step_name=hil_event_type,
            pipeline_state=full_payload,
            status="pending",
            created_at=datetime.now(UTC).isoformat(),
        )
        self.store.save(hitl_state)
        self.waiters.register(hil_id)

        envelope = build_hitl_request_envelope(
            hil_id=hil_id,
            hil_event_type=hil_event_type,
            session_id=session_id,
            payload=full_payload,
            streaming=streaming,
            title=title,
            message=message,
            options=options,
        )

        try:
            bus = self._bus()
            await bus.emit(
                StreamEvent(
                    event_type=EventType.HITL_REQUEST,
                    session_id=session_id,
                    data=envelope,
                )
            )
        except Exception:
            logger.warning("StreamBus emit HITL_REQUEST failed hil_id=%s", hil_id, exc_info=True)

        await persist_hitl_part(
            session_id=session_id,
            kind="hitl_request",
            hil_id=hil_id,
            hil_event_type=hil_event_type,
            payload=envelope,
        )

        return hil_id, hitl_request_to_sse(envelope)

    async def wait_for_decision(self, hil_id: str, *, timeout: float = 3600.0) -> HITLDecision:
        waiter_decision = await self.waiters.await_response(hil_id, timeout=timeout)
        return waiter_decision.to_hitl_decision()

    async def submit_response(
        self,
        hil_id: str,
        decision: HITLDecision,
        *,
        responder_user_id: str,
        session_id: str | None = None,
    ) -> None:
        """Store terminal state, emit HITL_RESPONSE on StreamBus, wake waiters."""
        hitl_state = self.store.load(hil_id)
        if hitl_state is None:
            raise ValueError(f"HITL state not found: {hil_id}")
        if hitl_state.status != "pending":
            raise ValueError(f"HITL already resolved: {hitl_state.status}")

        sid = session_id or str(hitl_state.pipeline_state.get("session_id") or "")
        hitl_state.status = decision.action
        hitl_state.feedback = decision.feedback
        hitl_state.edited_state = decision.edited_state
        hitl_state.resolved_at = datetime.now(UTC).isoformat()
        self.store.save(hitl_state)

        response_data = {
            "hil_id": hil_id,
            "hil_event_type": hitl_state.step_name,
            "session_id": sid,
            "response_option_id": decision.action,
            "responder_user_id": responder_user_id,
            "feedback": decision.feedback,
            "edited_state": decision.edited_state,
            "metadata": {"timeout_default_applied": False},
        }

        try:
            bus = self._bus()
            await bus.emit(
                StreamEvent(
                    event_type=EventType.HITL_RESPONSE,
                    session_id=sid,
                    data=response_data,
                )
            )
        except Exception:
            logger.warning("StreamBus emit HITL_RESPONSE failed hil_id=%s", hil_id, exc_info=True)

        self.waiters.resolve(
            HitlWaiterDecision(
                hil_id=hil_id,
                response_option_id=decision.action,
                responder_user_id=responder_user_id,
                feedback=decision.feedback,
                edited_state=decision.edited_state,
            )
        )

        if sid:
            await persist_hitl_part(
                session_id=sid,
                kind="hitl_response",
                hil_id=hil_id,
                hil_event_type=hitl_state.step_name,
                payload=response_data,
                responder_user_id=responder_user_id,
                response_option_id=decision.action,
            )

    def get(self, hil_id: str) -> HITLState | None:
        return self.store.load(hil_id)


def get_hitl_coordinator() -> HitlCoordinator:
    """Deep-search HITL coordinator singleton (isolated store)."""
    global _coordinator
    if _coordinator is None:
        from pathlib import Path

        from ..agent.bootstrap import get_runtime

        backend_root = Path(__file__).resolve().parents[1]
        store = HITLStore(data_dir=backend_root / "data" / "hitl_deep_search")

        def _bus():
            return get_runtime().stream_bus

        _coordinator = HitlCoordinator(store=store, stream_bus_getter=_bus)
    return _coordinator


def reset_hitl_coordinator() -> None:
    global _coordinator
    _coordinator = None
