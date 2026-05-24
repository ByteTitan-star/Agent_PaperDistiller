"""DebatePattern — proposer vs critic with adjudication."""

from __future__ import annotations

from typing import Any

from .._types import AgentResult, CollaborationResult
from ..events import EventBus
from ...harness.agents.base import BaseAgent
from .base import BaseCollaborationPattern


class DebatePattern(BaseCollaborationPattern):
    """Two-agent debate: one proposes, one critiques, then adjudicate.

    Maps to the existing ToT Gen+Eval flow.

    Args:
        proposer: Agent that generates proposals (e.g. DeepSeekAgent).
        critic: Agent that evaluates proposals (e.g. QwenAgent).
        rounds: Number of debate rounds (default 1).
    """

    def __init__(
        self,
        proposer: BaseAgent,
        critic: BaseAgent,
        event_bus: EventBus,
        rounds: int = 1,
    ) -> None:
        super().__init__(
            name="debate",
            agents=[proposer, critic],
            event_bus=event_bus,
        )
        self.rounds = rounds

    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        self._emit("debate_start", {"rounds": self.rounds})

        trace: list[dict[str, Any]] = []
        all_proposals: list[AgentResult] = []
        all_reviews: list[AgentResult] = []

        proposer = self.agents[0]
        critic = self.agents[1]

        for round_idx in range(self.rounds):
            self._emit("round_start", {"round": round_idx + 1})

            # Phase 1: Proposer generates
            proposal = await proposer.execute(input_text, **kwargs)
            trace.append({
                "round": round_idx + 1,
                "role": "proposer",
                "agent": proposer.name,
                "content_preview": str(proposal.content)[:200] if proposal.content else None,
                "error": proposal.error,
            })
            all_proposals.append(proposal)

            if proposal.error:
                self._emit("proposer_error", {"round": round_idx + 1, "error": proposal.error})
                continue

            # Phase 2: Critic reviews
            review_input = str(proposal.content)
            review = await critic.execute(review_input, **kwargs)
            trace.append({
                "round": round_idx + 1,
                "role": "critic",
                "agent": critic.name,
                "content_preview": str(review.content)[:200] if review.content else None,
                "error": review.error,
            })
            all_reviews.append(review)

            self._emit("round_end", {"round": round_idx + 1})

        # Phase 3: Adjudicate — pick best proposal weighted by reviews
        final = self._adjudicate(all_proposals, all_reviews)

        self._emit("debate_end", {"rounds_completed": self.rounds})
        return CollaborationResult(
            final_output=final,
            participants=[a.name for a in self.agents],
            rounds=self.rounds,
            trace=trace,
        )

    def _adjudicate(
        self,
        proposals: list[AgentResult],
        reviews: list[AgentResult],
    ) -> Any:
        # Return the best non-error proposal; if all errored, return last one.
        for proposal in proposals:
            if proposal.content and not proposal.error:
                return proposal.content
        return proposals[-1].content if proposals else None
