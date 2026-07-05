"""HITL 中间件 — 将 HITL 检查点集成到流水线步骤中。

提供简洁的 API，让流水线步骤在执行前检查是否需要人工审批：
    if hitl_middleware.has_checkpoint(step_name):
        decision = await hitl_middleware.check(step_name, state)
        if decision.action == "rejected":
            raise PipelineAborted(...)
        if decision.action == "edited":
            state.update(decision.edited_state)
"""

from __future__ import annotations

from typing import Any

from .base import HITLDecision, HITLManager


class HITLMiddleware:
    """HITL 检查点中间件，简化流水线步骤中的人工审批逻辑。

    封装了 HITLManager 的 interrupt + wait_for_decision 两步操作，
    提供一步到位的 check() 方法。

    在 PipelineStep 中的使用方式：
        if hitl_middleware.has_checkpoint(step_name):
            decision = await hitl_middleware.check(step_name, state)
            if decision.action == "rejected":
                raise PipelineAborted(...)
            if decision.action == "edited":
                state.update(decision.edited_state)

    Attributes:
        hitl_manager: 底层的 HITL 管理器。
    """

    def __init__(self, hitl_manager: HITLManager) -> None:
        self.hitl_manager = hitl_manager

    def has_checkpoint(self, step_name: str) -> bool:
        """检查某个步骤是否需要人工审批。

        Args:
            step_name: 步骤名称。

        Returns:
            bool: 是否有检查点。
        """
        return self.hitl_manager.has_checkpoint(step_name)

    async def check(self, step_name: str, state: dict[str, Any]) -> HITLDecision:
        """执行 HITL 检查：如果有检查点则暂停等待人工决策。

        等价于：interrupt(step_name, state) → wait_for_decision(hitl_id)

        Args:
            step_name: 步骤名称。
            state: 当前流水线状态快照。

        Returns:
            HITLDecision: 人工做出的决策（approved / rejected / edited）。
        """
        hitl_state = await self.hitl_manager.interrupt(step_name, state)
        decision = await self.hitl_manager.wait_for_decision(hitl_state.id)
        return decision
