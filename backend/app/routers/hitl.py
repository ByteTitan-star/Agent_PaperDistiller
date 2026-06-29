"""HITL 决策端点 — 提交深度搜索中的人工审批决策。

当 deep_search_stream() 在检查点暂停等待时，前端通过此 API 提交用户决策：
- approved: 批准继续
- rejected: 取消中止
- edited: 修改参数后继续（如修改搜索问题或追加搜索关键词）
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.dependencies import get_current_user
from ..harness.hitl._types import HITLDecision
from ..models import User
from ..services.chat import get_deep_search_hitl

router = APIRouter(tags=["hitl"])


class HITLDecisionRequest(BaseModel):
    """人工审批决策请求。"""
    action: Literal["approved", "rejected", "edited"] = Field(
        description="决策类型：approved（继续）/ rejected（取消）/ edited（修改后继续）",
    )
    feedback: str | None = Field(
        default=None,
        description="可选的用户反馈文本",
    )
    edited_state: dict[str, Any] | None = Field(
        default=None,
        description=(
            "修改后的状态（仅 action='edited' 时使用）。"
            "pre_search 检查点：{question, clarification}；"
            "pre_report 检查点：{extra_keywords}"
        ),
    )


class HITLDecisionResponse(BaseModel):
    """决策提交响应。"""
    ok: bool
    hitl_id: str
    action: str


@router.post("/hitl/{hitl_id}/decide", response_model=HITLDecisionResponse)
async def decide_hitl(
    hitl_id: str,
    payload: HITLDecisionRequest,
    user: User = Depends(get_current_user),
):
    """提交 HITL 决策，唤醒等待中的 deep_search_stream。

    前端页面：WorkspaceView（工作区页）右侧聊天面板中的审批弹窗
    用户操作：深度研究流程中弹出审批面板 → 用户点击「批准」/「取消」/「修改后继续」
    说明：调用后 deep_search_stream 中等待的 asyncio.Event 会被 set，流程恢复
    """
    hitl_manager = get_deep_search_hitl()

    # 校验 HITL 状态存在且仍在等待
    hitl_state = hitl_manager.get(hitl_id)
    if hitl_state is None:
        raise HTTPException(status_code=404, detail="HITL 状态不存在")
    if hitl_state.status != "pending":
        raise HTTPException(status_code=400, detail=f"HITL 状态已处理（{hitl_state.status}）")

    decision = HITLDecision(
        action=payload.action,
        feedback=payload.feedback,
        edited_state=payload.edited_state,
    )
    await hitl_manager.decide(hitl_id, decision)

    return HITLDecisionResponse(ok=True, hitl_id=hitl_id, action=payload.action)


@router.get("/hitl/{hitl_id}")
async def get_hitl_state(
    hitl_id: str,
    user: User = Depends(get_current_user),
):
    """查询 HITL 审批状态（轮询备用）。

    前端页面：WorkspaceView（工作区页）深度研究流程内部使用
    用户操作：深度研究中前端轮询检查审批状态，无直接用户操作
    """
    hitl_manager = get_deep_search_hitl()
    hitl_state = hitl_manager.get(hitl_id)
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
