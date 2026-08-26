"""Smoke tests for CI (full deps) and local lint adjacency."""

from __future__ import annotations


def test_agent_schemas_import() -> None:
    from app.agent.schemas import AuthContext, EventType

    auth = AuthContext(user_id=1, paper_id="p1")
    assert auth.user_id == 1
    assert EventType.DONE.value == "done"


def test_agent_schemas_hitl_event_types() -> None:
    from app.agent.schemas import EventType

    assert EventType.HITL_REQUEST.value == "hitl_request"
    assert EventType.HITL_RESPONSE.value == "hitl_response"


def test_agent_errors_import() -> None:
    from app.agent.errors import AgentRuntimeError, MaxIterationsError

    assert issubclass(MaxIterationsError, AgentRuntimeError)
