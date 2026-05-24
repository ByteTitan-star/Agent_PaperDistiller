"""CollaborationRegistry — register and retrieve collaboration patterns."""

from __future__ import annotations

from typing import Any

from ..events import EventBus
from ...harness.agents.base import BaseAgent
from .base import BaseCollaborationPattern
from .debate import DebatePattern
from .round_robin import RoundRobinPattern
from .supervisor import SupervisorPattern


_BUILTIN_PATTERNS: dict[str, type[BaseCollaborationPattern]] = {
    "debate": DebatePattern,
    "supervisor": SupervisorPattern,
    "round_robin": RoundRobinPattern,
}


class CollaborationRegistry:
    """Creates and caches collaboration pattern instances."""

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self._patterns: dict[str, BaseCollaborationPattern] = {}
        self._custom: dict[str, type[BaseCollaborationPattern]] = {}

    def register_pattern(self, name: str, cls: type[BaseCollaborationPattern]) -> None:
        self._custom[name] = cls

    def create(
        self,
        mode: str,
        agents: list[BaseAgent],
        **kwargs: Any,
    ) -> BaseCollaborationPattern:
        key = (mode, tuple(a.name for a in agents))
        if key in self._patterns:
            return self._patterns[key]

        cls = self._custom.get(mode) or _BUILTIN_PATTERNS.get(mode)
        if cls is None:
            raise ValueError(f"Unknown collaboration pattern: {mode}")

        if mode == "debate":
            pattern = DebatePattern(
                proposer=agents[0],
                critic=agents[1],
                event_bus=self.event_bus,
                rounds=kwargs.get("rounds", 1),
            )
        elif mode == "supervisor":
            pattern = SupervisorPattern(
                supervisor=agents[0],
                workers=agents[1:],
                event_bus=self.event_bus,
                merge_prompt_template=kwargs.get("merge_prompt_template"),
            )
        elif mode == "round_robin":
            pattern = RoundRobinPattern(
                agents=agents,
                event_bus=self.event_bus,
                rounds=kwargs.get("rounds", 1),
                refinement_prompt=kwargs.get("refinement_prompt"),
            )
        else:
            pattern = cls(name=mode, agents=agents, event_bus=self.event_bus)

        self._patterns[key] = pattern
        return pattern
