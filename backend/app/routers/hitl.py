"""HITL 决策端点 — submit HITL_RESPONSE via StreamBus + coordinator."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_user
from ..harness.hitl._types import HITLDecision
from ..models import User
from ..services.hitl_coordinator import get_hitl_coordinator

router = APIRouter(tags=["hitl"])


class HITLDecisionRequest(BaseModel):
    action: Literal["approved", "rejected", "edited"] = Field(
        description="决策类型：approved / rejected / edited",
    )
    feedback: str | None = None
    edited_state: dict[str, Any] | None = None
    session_id: str | None = Field(
        default=None,
        description="Chat session_id（可选，用于 StreamBus 路由）",
    )


class HITLDecisionResponse(BaseModel):
    ok: bool
    hitl_id: str
    action: str


@router.post("/hitl/{hitl_id}/decide", response_model=HITLDecisionResponse)
async def decide_hitl(
    hitl_id: str,
    payload: HITLDecisionRequest,
    user: User = Depends(get_current_user),
):
    """Submit HITL decision → store + HITL_RESPONSE on StreamBus → wake waiters."""
    coordinator = get_hitl_coordinator()
    hitl_state = coordinator.get(hitl_id)
    if hitl_state is None:
        raise HTTPException(status_code=404, detail="HITL 状态不存在")
    if hitl_state.status != "pending":
        raise HTTPException(status_code=400, detail=f"HITL 状态已处理（{hitl_state.status}）")

    decision = HITLDecision(
        action=payload.action,
        feedback=payload.feedback,
        edited_state=payload.edited_state,
    )
    sid = payload.session_id or str(hitl_state.pipeline_state.get("session_id") or "")
    await coordinator.submit_response(
        hitl_id,
        decision,
        responder_user_id=str(user.id),
        session_id=sid or None,
    )
    return HITLDecisionResponse(ok=True, hitl_id=hitl_id, action=payload.action)


@router.get("/hitl/{hitl_id}")
async def get_hitl_state(
    hitl_id: str,
    user: User = Depends(get_current_user),
):
    coordinator = get_hitl_coordinator()
    hitl_state = coordinator.get(hitl_id)
    if hitl_state is None:
        raise HTTPException(status_code=404, detail="HITL 状态不存在")
    return {
        "id": hitl_state.id,
        "step_name": hitl_state.step_name,
        "status": hitl_state.status,
        "feedback": hitl_state.feedback,
        "pipeline_state": hitl_state.pipeline_state,
        "created_at": hitl_state.created_at,
        "resolved_at": hitl_state.resolved_at,
    }
