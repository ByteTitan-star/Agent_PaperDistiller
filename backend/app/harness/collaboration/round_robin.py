"""RoundRobinPattern — agents take turns building on each other's output."""

from __future__ import annotations

from typing import Any

from .._types import CollaborationResult
from ..events import EventBus
from ...harness.agents.base import BaseAgent
from .base import BaseCollaborationPattern


class RoundRobinPattern(BaseCollaborationPattern):
    """Agents take turns, each building on the previous agent's output.

    Flow:
        Agent1 processes input → Agent2 refines Agent1's output → ...
        Optionally loops for multiple rounds.

    Args:
        agents: Ordered list of agents. Each receives the previous output.
        rounds: How many times to cycle through all agents (default 1).
        refinement_prompt: Template with {previous_output} placeholder.
    """

    def __init__(
        self,
        agents: list[BaseAgent],
        event_bus: EventBus,
        rounds: int = 1,
        refinement_prompt: str | None = None,
    ) -> None:
        super().__init__(name="round_robin", agents=agents, event_bus=event_bus)
        self.rounds = rounds
        self.refinement_prompt = refinement_prompt or (
            "以下是前一个处理步骤的输出，请在它的基础上进一步改进和完善：\n\n"
            "{previous_output}\n\n请直接输出改进后的结果。"
        )

    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        self._emit("round_robin_start", {"agents": len(self.agents), "rounds": self.rounds})

        trace: list[dict[str, Any]] = []
        current = input_text

        for round_idx in range(self.rounds):
            for agent_idx, agent in enumerate(self.agents):
                step_label = f"R{round_idx + 1}-{agent.name}"

                if agent_idx == 0 and round_idx == 0:
                    prompt = current
                else:
                    prompt = self.refinement_prompt.format(previous_output=current)

                result = await agent.execute(prompt, **kwargs)
                trace.append({
                    "round": round_idx + 1,
                    "agent": agent.name,
                    "agent_index": agent_idx,
                    "error": result.error,
                    "content_preview": str(result.content)[:200] if result.content else None,
                })

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
