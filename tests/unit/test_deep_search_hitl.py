"""Deep search HITL orchestration tests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.harness.hitl._types import HITLDecision
from app.services.deep_search_hitl import stream_deep_search


def _payloads(events: list[str]) -> list[dict]:
    out = []
    for event in events:
        if event.startswith("data: "):
            out.append(json.loads(event[6:].strip()))
    return out


def _mock_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.request = AsyncMock(
        side_effect=[
            ("hil-pre-search", 'data: {"type": "hitl_request", "hil_id": "hil-pre-search"}\n\n'),
            ("hil-pre-report", 'data: {"type": "hitl_request", "hil_id": "hil-pre-report", "streaming": true}\n\n'),
        ]
    )
    coord.wait_for_decision = AsyncMock(
        side_effect=[
            HITLDecision(action="approved"),
            HITLDecision(action="approved"),
        ]
    )
    return coord


@pytest.mark.asyncio
async def test_pre_report_tokens_stream_before_done() -> None:
    async def fake_agent_stream(*_args, **kwargs):
        defer = kwargs.get("defer_done")
        holder = kwargs.get("done_holder")
        yield 'data: {"type": "tool", "name": "web_search"}\n\n'
        yield 'data: {"type": "source", "title": "https://example.com", "url": "https://example.com"}\n\n'
        yield 'data: {"type": "token", "text": "Hello"}\n\n'
        yield 'data: {"type": "token", "text": " world"}\n\n'
        done = {
            "type": "done",
            "answer": "Hello world",
            "thinking_chain": [],
            "sources": ["https://example.com"],
        }
        if defer and holder is not None:
            holder["payload"] = done
        else:
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    collected: list[str] = []
    with (
        patch("app.services.deep_search_hitl.log_separator"),
        patch("app.services.deep_search_hitl.log_research_plan"),
        patch(
            "app.services.deep_search_hitl.generate_research_plan",
            new=AsyncMock(return_value={"understanding": ["u"], "search_plan": []}),
        ),
        patch("app.services.agent_chat.agent_deep_search_stream", fake_agent_stream),
    ):
        async for event in stream_deep_search(
            question="test question long enough",
            contexts=["ctx"],
            settings=SimpleNamespace(
                supervisor_planning_enabled=False,
                react_enable_clarification=False,
            ),
            user_id=1,
            paper_id="p1",
            hitl_coordinator=_mock_coordinator(),
            chat_session_id="sess-1",
            agent_session_id="run-1",
        ):
            collected.append(event)

    types = [p["type"] for p in _payloads(collected)]
    assert types.count("token") == 2
    hitl_reqs = [p for p in _payloads(collected) if p.get("type") == "hitl_request"]
    assert len(hitl_reqs) == 2
    assert hitl_reqs[1].get("streaming") is True
    assert types[-1] == "done"


@pytest.mark.asyncio
async def test_pre_search_rejection_ends_with_cancel_message() -> None:
    coord = MagicMock()
    coord.request = AsyncMock(
        return_value=("hil-pre-search", 'data: {"type": "hitl_request", "hil_id": "hil-pre-search"}\n\n')
    )
    coord.wait_for_decision = AsyncMock(return_value=HITLDecision(action="rejected"))

    collected: list[str] = []
    with (
        patch("app.services.deep_search_hitl.log_separator"),
        patch("app.services.deep_search_hitl.log_research_plan"),
        patch(
            "app.services.deep_search_hitl.generate_research_plan",
            new=AsyncMock(return_value={"understanding": ["u"], "search_plan": []}),
        ),
    ):
        async for event in stream_deep_search(
            question="test question long enough",
            contexts=["ctx"],
            settings=SimpleNamespace(
                supervisor_planning_enabled=False,
                react_enable_clarification=False,
            ),
            user_id=1,
            paper_id="p1",
            hitl_coordinator=coord,
            chat_session_id="sess-1",
        ):
            collected.append(event)

    payloads = _payloads(collected)
    assert payloads[-1]["type"] == "done"
    assert "取消" in payloads[-1]["answer"]
    coord.wait_for_decision.assert_awaited_once_with("hil-pre-search", timeout=3600.0)
