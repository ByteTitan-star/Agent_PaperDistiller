"""POST /hitl/{id}/decide — coordinator wiring (no full auth stack import)."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.harness.hitl._types import HITLDecision, HITLState


def _import_hitl_router():
    """Import hitl router with lightweight stubs for optional auth/DB deps."""
    jose = MagicMock()
    jose.JWTError = Exception
    jose.jwt = MagicMock()
    stubs = {
        "jose": jose,
        "passlib": MagicMock(),
        "passlib.context": MagicMock(),
        "bcrypt": MagicMock(),
    }
    with patch.dict(sys.modules, stubs):
        return importlib.import_module("app.routers.hitl")


@pytest.mark.asyncio
async def test_decide_hitl_submits_response_with_session_id() -> None:
    hitl_mod = _import_hitl_router()
    pending = HITLState(
        id="hil-api",
        step_name="deep_search_pre_search",
        pipeline_state={"session_id": "chat-sess"},
        status="pending",
        created_at="2026-01-01T00:00:00Z",
    )
    coord = MagicMock()
    coord.get = MagicMock(return_value=pending)
    coord.submit_response = AsyncMock()
    user = MagicMock()
    user.id = 99

    with patch.object(hitl_mod, "get_hitl_coordinator", return_value=coord):
        resp = await hitl_mod.decide_hitl(
            "hil-api",
            hitl_mod.HITLDecisionRequest(action="approved", session_id="chat-sess"),
            user=user,
        )

    assert resp.ok is True
    assert resp.action == "approved"
    coord.submit_response.assert_awaited_once()
    args, kwargs = coord.submit_response.call_args
    assert args[0] == "hil-api"
    assert isinstance(args[1], HITLDecision)
    assert kwargs["responder_user_id"] == "99"
    assert kwargs["session_id"] == "chat-sess"


@pytest.mark.asyncio
async def test_decide_hitl_rejects_already_resolved() -> None:
    from fastapi import HTTPException

    hitl_mod = _import_hitl_router()
    resolved = HITLState(
        id="hil-done",
        step_name="deep_search_pre_search",
        pipeline_state={},
        status="approved",
        created_at="2026-01-01T00:00:00Z",
    )
    coord = MagicMock()
    coord.get = MagicMock(return_value=resolved)
    user = MagicMock()
    user.id = 1

    with patch.object(hitl_mod, "get_hitl_coordinator", return_value=coord), pytest.raises(HTTPException) as exc:
        await hitl_mod.decide_hitl(
            "hil-done",
            hitl_mod.HITLDecisionRequest(action="approved"),
            user=user,
        )
    assert exc.value.status_code == 400


def test_hitl_decision_request_accepts_session_id() -> None:
    hitl_mod = _import_hitl_router()
    req = hitl_mod.HITLDecisionRequest(action="edited", session_id="sess-x", edited_state={"question": "q"})
    assert req.session_id == "sess-x"
    assert req.action == "edited"
