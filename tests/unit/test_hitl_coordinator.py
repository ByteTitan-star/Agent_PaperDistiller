"""HitlCoordinator — StreamBus + waiter registry."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.schemas import EventType
from app.harness.hitl._types import HITLDecision
from app.harness.hitl.store import HITLStore
from app.services.hitl_coordinator import HitlCoordinator, reset_hitl_coordinator


@pytest.fixture(autouse=True)
def _reset_singleton() -> Generator[None]:
    reset_hitl_coordinator()
    yield
    reset_hitl_coordinator()


async def _submit_after_delay(
    coord: HitlCoordinator,
    hil_id: str,
    *,
    action: str = "approved",
    delay: float = 0.02,
) -> None:
    await asyncio.sleep(delay)
    with patch("app.services.hitl_coordinator.persist_hitl_part", new=AsyncMock()):
        await coord.submit_response(
            hil_id,
            HITLDecision(action=action),  # type: ignore[arg-type]
            responder_user_id="42",
            session_id="sess-a",
        )


@pytest.mark.asyncio
async def test_request_emits_hitl_request_on_stream_bus(tmp_path) -> None:
    store = HITLStore(data_dir=tmp_path)
    bus = MagicMock()
    bus.emit = AsyncMock()
    coord = HitlCoordinator(store=store, stream_bus_getter=lambda: bus)

    with patch("app.services.hitl_coordinator.persist_hitl_part", new=AsyncMock()):
        hil_id, sse = await coord.request(
            session_id="sess-a",
            user_id=1,
            paper_id="p1",
            hil_event_type="deep_search_pre_search",
            payload={"question": "q"},
            title="t",
            message="m",
        )

    assert hil_id
    assert "hitl_request" in sse
    bus.emit.assert_awaited()
    req_event = bus.emit.await_args[0][0]
    assert req_event.event_type == EventType.HITL_REQUEST
    assert req_event.session_id == "sess-a"
    assert req_event.data["hil_id"] == hil_id

    stored = store.load(hil_id)
    assert stored is not None
    assert stored.status == "pending"


@pytest.mark.asyncio
async def test_submit_response_emits_hitl_response_and_wakes_waiter(tmp_path) -> None:
    store = HITLStore(data_dir=tmp_path)
    bus = MagicMock()
    bus.emit = AsyncMock()
    coord = HitlCoordinator(store=store, stream_bus_getter=lambda: bus)

    with patch("app.services.hitl_coordinator.persist_hitl_part", new=AsyncMock()):
        _, sse = await coord.request(
            session_id="sess-a",
            user_id=1,
            paper_id="p1",
            hil_event_type="deep_search_pre_search",
            payload={"question": "q"},
            title="t",
            message="m",
        )

    hil_id = json.loads(sse.split("data: ", 1)[1])["hil_id"]

    decision, _ = await asyncio.gather(
        coord.wait_for_decision(hil_id, timeout=5.0),
        _submit_after_delay(coord, hil_id),
    )
    assert decision.action == "approved"

    response_calls = [c[0][0] for c in bus.emit.await_args_list]
    assert any(c.event_type == EventType.HITL_RESPONSE for c in response_calls)
    assert store.load(hil_id).status == "approved"


@pytest.mark.asyncio
async def test_double_submit_raises(tmp_path) -> None:
    store = HITLStore(data_dir=tmp_path)
    bus = MagicMock()
    bus.emit = AsyncMock()
    coord = HitlCoordinator(store=store, stream_bus_getter=lambda: bus)

    with patch("app.services.hitl_coordinator.persist_hitl_part", new=AsyncMock()):
        hil_id, _ = await coord.request(
            session_id="sess-a",
            user_id=1,
            paper_id="p1",
            hil_event_type="deep_search_pre_search",
            payload={},
            title="t",
            message="m",
        )
        await coord.submit_response(
            hil_id,
            HITLDecision(action="approved"),
            responder_user_id="1",
            session_id="sess-a",
        )

    with pytest.raises(ValueError, match="already resolved"):
        await coord.submit_response(
            hil_id,
            HITLDecision(action="rejected"),
            responder_user_id="1",
            session_id="sess-a",
        )


@pytest.mark.asyncio
async def test_sse_payload_from_request_is_parseable(tmp_path) -> None:
    store = HITLStore(data_dir=tmp_path)
    bus = MagicMock()
    bus.emit = AsyncMock()
    coord = HitlCoordinator(store=store, stream_bus_getter=lambda: bus)

    with patch("app.services.hitl_coordinator.persist_hitl_part", new=AsyncMock()):
        _, sse = await coord.request(
            session_id="sess-z",
            user_id=2,
            paper_id="p9",
            hil_event_type="deep_search_pre_report",
            payload={"phase": "pre_report"},
            streaming=True,
            title="边生成边审",
            message="请审核",
        )

    body = json.loads(sse.split("data: ", 1)[1].strip())
    assert body["type"] == "hitl_request"
    assert body["checkpoint"] == "pre_report"
    assert body["streaming"] is True
    assert body["hitl_id"] == body["hil_id"]
