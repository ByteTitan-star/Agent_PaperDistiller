"""多 Agent 协作模式基类 — 定义协作模式的抽象接口。

所有多 Agent 协作模式（辩论、轮询、监督者等）都继承此基类，
实现 run() 方法定义 Agent 之间的交互逻辑。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .._types import CollaborationResult, HarnessEvent
from ..events import EventBus
from ...harness.agents.base import BaseAgent


class BaseCollaborationPattern(ABC):
    """多 Agent 协作模式的抽象基类。

    每种协作模式定义了 Agent 之间如何交互：
    - 执行顺序（谁先谁后）
    - 轮次（单轮还是多轮）
    - 结果合并方式（投票、评分、直接取最终输出等）

    子类必须实现 run() 方法。

    Attributes:
        name: 协作模式名称，如 "debate" / "round_robin" / "supervisor"。
        agents: 参与协作的 Agent 列表。
        event_bus: 事件总线，用于发射协作过程中的事件。
    """

    name: str
    agents: list[BaseAgent]
    event_bus: EventBus

    def __init__(
        self,
        name: str,                   # 协作模式名称
        agents: list[BaseAgent],      # 参与 Agent 列表
        event_bus: EventBus,          # 事件总线
    ) -> None:
        self.name = name
        self.agents = agents
        self.event_bus = event_bus

    @abstractmethod
    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        """执行协作流程，子类必须实现。

        Args:
            input_text: 初始输入文本。
            **kwargs: 附加参数，传递给各 Agent。

        Returns:
            CollaborationResult: 协作结果（最终输出、参与者、轮次、追踪记录）。
        """
        ...

    def _emit(self, action: str, payload: dict[str, Any] | None = None) -> None:
        """发射协作事件到事件总线的便捷方法。

        自动填充 layer="collaboration" 和 component=self.name。

        Args:
            action: 动作名称，如 "debate_start" / "round_start"。
            payload: 附带数据字典。
        """
        self.event_bus.emit(
            HarnessEvent(
                layer="collaboration",
                component=self.name,
                action=action,
                payload=payload or {},
            )
        )
