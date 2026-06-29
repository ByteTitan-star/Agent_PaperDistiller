"""HITLManager — 人机协同管理器，处理流水线的中断-恢复循环。

核心流程：
    1. interrupt(step_name, state) — 暂停流水线，保存状态，等待人工决策
    2. 外部系统（API / UI）调用 decide(hitl_id, decision) — 提交人工决策
    3. wait_for_decision(hitl_id) — 流水线端阻塞等待，直到人工做出决策

使用 asyncio.Event 实现异步等待，不占用 CPU 资源。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .._types import HarnessEvent
from ..events import EventBus
from ._types import HITLCheckpoint, HITLDecision, HITLState
from .store import HITLStore


class HITLManager:
    """管理流水线步骤的人工审批流程。

    使用方式：
        # 流水线端（等待人工）
        hitl_state = await manager.interrupt("critique", current_state)
        decision = await manager.wait_for_decision(hitl_state.id)
        if decision.action == "approved":
            # 继续执行
        elif decision.action == "edited":
            # 使用 decision.edited_state 继续执行

        # API/UI 端（提交决策）
        await manager.decide(hitl_state.id, HITLDecision(action="approved"))

    Attributes:
        event_bus: 事件总线。
        store: HITL 状态持久化存储。
        checkpoints: 检查点配置字典，key 为步骤名。
        poll_interval: 已废弃（现用 asyncio.Event 替代轮询）。
        _waiters: 等待中的 asyncio.Event 字典，key 为 HITL state ID。
    """

    def __init__(
        self,
        event_bus: EventBus,                         # 事件总线
        store: HITLStore | None = None,               # 持久化存储（默认内存）
        checkpoints: list[str] | None = None,         # 需要审批的步骤名列表
        poll_interval: float = 2.0,                   # 轮询间隔（已废弃）
    ) -> None:
        self.event_bus = event_bus
        self.store = store or HITLStore()
        self.checkpoints: dict[str, HITLCheckpoint] = {}
        self.poll_interval = poll_interval
        self._waiters: dict[str, asyncio.Event] = {}  # hitl_id → asyncio.Event

        # 将步骤名列表转为 HITLCheckpoint 字典
        for name in (checkpoints or []):
            self.checkpoints[name] = HITLCheckpoint(step_name=name)

    def has_checkpoint(self, step_name: str) -> bool:
        """检查某个步骤是否配置了人工审批检查点。

        Args:
            step_name: 步骤名称。

        Returns:
            bool: 是否有检查点。
        """
        return step_name in self.checkpoints

    async def interrupt(self, step_name: str, state: dict[str, Any]) -> HITLState:
        """暂停流水线，创建待审批状态，等待人工决策。

        此方法不阻塞，只创建状态和等待事件。
        需配合 wait_for_decision() 使用。

        Args:
            step_name: 触发检查点的步骤名称。
            state: 当前流水线状态快照。

        Returns:
            HITLState: 待审批的状态对象（包含 id，用于后续操作）。
        """
        from uuid import uuid4

        hitl_state = HITLState(
            id=uuid4().hex,
            step_name=step_name,
            pipeline_state=dict(state),
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        # 持久化保存
        self.store.save(hitl_state)
        # 创建异步等待事件
        self._waiters[hitl_state.id] = asyncio.Event()

        self.event_bus.emit(
            HarnessEvent(
                layer="hitl",
                component=step_name,
                action="interrupted",
                payload={"hitl_id": hitl_state.id},
            )
        )
        return hitl_state

    async def wait_for_decision(self, hitl_id: str, timeout: float = 3600.0) -> HITLDecision:
        """阻塞等待人工决策（异步）。

        使用 asyncio.Event 实现，不占用 CPU。
        超时后会抛出 asyncio.TimeoutError。

        Args:
            hitl_id: HITL 状态 ID（由 interrupt() 返回）。
            timeout: 等待超时时间（秒），默认 1 小时。

        Returns:
            HITLDecision: 人工做出的决策。

        Raises:
            ValueError: 未知的 hitl_id。
            asyncio.TimeoutError: 等待超时。
        """
        evt = self._waiters.get(hitl_id)
        if evt is None:
            raise ValueError(f"Unknown HITL id: {hitl_id}")

        # 异步等待，直到 decide() 调用 evt.set()
        await asyncio.wait_for(evt.wait(), timeout=timeout)

        # 从存储中读取更新后的状态
        hitl_state = self.store.load(hitl_id)
        if hitl_state is None:
            raise ValueError(f"HITL state not found: {hitl_id}")

        return HITLDecision(
            action=hitl_state.status,  # type: ignore[arg-type]
            feedback=hitl_state.feedback,
            edited_state=hitl_state.edited_state,
        )

    async def decide(self, hitl_id: str, decision: HITLDecision) -> None:
        """提交人工决策（由 API / UI 端调用）。

        更新 HITL 状态并唤醒等待中的流水线。

        Args:
            hitl_id: HITL 状态 ID。
            decision: 人工决策对象。

        Raises:
            ValueError: 未找到对应的 HITL 状态。
        """
        hitl_state = self.store.load(hitl_id)
        if hitl_state is None:
            raise ValueError(f"HITL state not found: {hitl_id}")

        # 更新状态
        hitl_state.status = decision.action
        hitl_state.feedback = decision.feedback
        hitl_state.edited_state = decision.edited_state
        hitl_state.resolved_at = datetime.now(timezone.utc).isoformat()
        self.store.save(hitl_state)

        self.event_bus.emit(
            HarnessEvent(
                layer="hitl",
                component=hitl_state.step_name,
                action=decision.action,
                payload={"hitl_id": hitl_id, "feedback": decision.feedback},
            )
        )

        # 唤醒等待中的流水线
        evt = self._waiters.get(hitl_id)
        if evt:
            evt.set()

    def list_pending(self) -> list[HITLState]:
        """获取所有待审批的状态列表。

        Returns:
            list[HITLState]: 状态为 "pending" 的 HITL 状态列表。
        """
        return self.store.list_by_status("pending")

    def get(self, hitl_id: str) -> HITLState | None:
        """根据 ID 获取 HITL 状态。

        Args:
            hitl_id: HITL 状态 ID。

        Returns:
            HITLState | None: 对应的状态，未找到则返回 None。
        """
        return self.store.load(hitl_id)
