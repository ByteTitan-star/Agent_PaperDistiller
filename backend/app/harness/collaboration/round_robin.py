"""轮询模式（RoundRobinPattern）— 多个 Agent 轮流改进输出。

流程：
    Agent1 处理输入 → Agent2 改进 Agent1 的输出 → Agent3 改进 Agent2 的输出 → ...
    可选多轮循环（rounds > 1），每轮在上一轮最终输出基础上继续改进。

每个 Agent 接收前一个 Agent 的输出，通过 refinement_prompt 模板包装后作为新输入。
"""

from __future__ import annotations

from typing import Any

from .._types import CollaborationResult
from ..events import EventBus
from ...harness.agents.base import BaseAgent
from .base import BaseCollaborationPattern


class RoundRobinPattern(BaseCollaborationPattern):
    """Agent 轮流改进模式：每个 Agent 在前一个 Agent 输出的基础上继续改进。

    流程：
        第 1 轮第 1 个 Agent：处理原始输入
        第 1 轮第 2 个 Agent：改进第 1 个 Agent 的输出
        第 1 轮第 3 个 Agent：改进第 2 个 Agent 的输出
        ...（如果 rounds > 1，继续循环）

    Attributes:
        rounds: 循环轮次数，默认 1。
        refinement_prompt: 改进提示词模板，必须包含 {previous_output} 占位符。
    """

    def __init__(
        self,
        agents: list[BaseAgent],             # 按顺序参与的 Agent 列表
        event_bus: EventBus,                  # 事件总线
        rounds: int = 1,                      # 循环轮次
        refinement_prompt: str | None = None,  # 自定义改进模板
    ) -> None:
        super().__init__(name="round_robin", agents=agents, event_bus=event_bus)
        self.rounds = rounds
        # 默认改进模板：要求在前一个输出基础上进一步完善
        self.refinement_prompt = refinement_prompt or (
            "以下是前一个处理步骤的输出，请在它的基础上进一步改进和完善：\n\n"
            "{previous_output}\n\n请直接输出改进后的结果。"
        )

    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        """执行轮询改进流程。

        Args:
            input_text: 初始输入文本。
            **kwargs: 附加参数，传递给每个 Agent。

        Returns:
            CollaborationResult: 最终改进结果，包含所有参与者和追踪记录。
        """
        self._emit("round_robin_start", {"agents": len(self.agents), "rounds": self.rounds})

        trace: list[dict[str, Any]] = []
        current = input_text  # 当前文本（逐步被改进）

        for round_idx in range(self.rounds):
            for agent_idx, agent in enumerate(self.agents):
                step_label = f"R{round_idx + 1}-{agent.name}"

                if agent_idx == 0 and round_idx == 0:
                    # 第一个 Agent 的第一轮直接使用原始输入
                    prompt = current
                else:
                    # 后续 Agent 使用改进模板包装前一个输出
                    prompt = self.refinement_prompt.format(previous_output=current)

                result = await agent.execute(prompt, **kwargs)
                trace.append({
                    "round": round_idx + 1,
                    "agent": agent.name,
                    "agent_index": agent_idx,
                    "error": result.error,
                    "content_preview": str(result.content)[:200] if result.content else None,
                })

                # 只在成功时更新当前文本（失败则保持不变）
                if result.content and not result.error:
                    current = str(result.content)

                self._emit("step_complete", {
                    "round": round_idx + 1,
                    "agent": agent.name,
                    "has_error": result.error is not None,
                })

        self._emit("round_robin_end")
        return CollaborationResult(
            final_output=current,
            participants=[a.name for a in self.agents],
            rounds=self.rounds,
            trace=trace,
        )
