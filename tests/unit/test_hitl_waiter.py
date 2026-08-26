"""HitlWaiterRegistry — in-process future-based HITL await."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.hitl import HitlWaiterDecision, HitlWaiterRegistry
from app.harness.hitl._types import HITLDecision


@pytest.mark.asyncio
async def test_waiter_register_and_resolve() -> None:
    reg = HitlWaiterRegistry()
    reg.register("hil-1")

    async def resolve_soon() -> None:
        await asyncio.sleep(0.02)
        reg.resolve(
            HitlWaiterDecision(
                hil_id="hil-1",
                response_option_id="approved",
                responder_user_id="7",
            )
        )

    decision, _ = await asyncio.gather(reg.await_response("hil-1", timeout=5.0), resolve_soon())
    assert decision.response_option_id == "approved"
    hitl = decision.to_hitl_decision()
    assert isinstance(hitl, HITLDecision)
    assert hitl.action == "approved"


@pytest.mark.asyncio
async def test_waiter_duplicate_register_raises() -> None:
    reg = HitlWaiterRegistry()
    reg.register("dup")
    with pytest.raises(RuntimeError, match="already registered"):
        reg.register("dup")


@pytest.mark.asyncio
async def test_waiter_cancel_leaves_future_unresolved() -> None:
    reg = HitlWaiterRegistry()
    reg.register("c1")
    reg.cancel("c1")
    reg.resolve(
        HitlWaiterDecision(
            hil_id="c1",
            response_option_id="approved",
            responder_user_id="1",
        )
    )
    with pytest.raises(asyncio.TimeoutError):
        await reg.await_response("c1", timeout=0.05)
