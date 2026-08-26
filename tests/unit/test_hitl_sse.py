"""HITL SSE envelope mapping — bioagent-compatible wire format."""

from __future__ import annotations

import json

from app.services.hitl_sse import (
    build_hitl_request_envelope,
    hitl_preview_to_sse,
    hitl_request_to_sse,
)


def test_build_hitl_request_envelope_has_biomap_hil_wrapper() -> None:
    env = build_hitl_request_envelope(
        hil_id="abc123",
        hil_event_type="deep_search_pre_search",
        session_id="sess-1",
        payload={"question": "What is X?"},
        title="确认计划",
        message="请确认",
        streaming=False,
    )
    assert env["hil_id"] == "abc123"
    assert env["hil_event_type"] == "deep_search_pre_search"
    assert env["session_id"] == "sess-1"
    assert env["streaming"] is False
    assert env["options"]
    assert env["biomap_hil"]["event"] == "hil_request"
    assert env["biomap_hil"]["data"]["hil_id"] == "abc123"


def test_hitl_request_to_sse_emits_primary_and_legacy_fields() -> None:
    env = build_hitl_request_envelope(
        hil_id="x1",
        hil_event_type="deep_search_pre_report",
        session_id="sess-2",
        payload={"phase": "pre_report", "sources": []},
        streaming=True,
        title="边生成边审",
        message="请审核",
    )
    sse = hitl_request_to_sse(env)
    assert sse.startswith("data: ")
    body = json.loads(sse.split("data: ", 1)[1].strip())
    assert body["type"] == "hitl_request"
    assert body["type_legacy"] == "hitl_approval"
    assert body["hitl_id"] == "x1"
    assert body["hil_id"] == "x1"
    assert body["checkpoint"] == "pre_report"
    assert body["streaming"] is True
    assert body["current_state"]["phase"] == "pre_report"


def test_hitl_preview_to_sse() -> None:
    sse = hitl_preview_to_sse(checkpoint="pre_report", answer_preview="Hello", source_count=3)
    body = json.loads(sse.split("data: ", 1)[1].strip())
    assert body["type"] == "hitl_preview"
    assert body["answer_preview"] == "Hello"
    assert body["source_count"] == 3
