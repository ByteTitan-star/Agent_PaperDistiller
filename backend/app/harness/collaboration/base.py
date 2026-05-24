"""Base collaboration pattern for multi-agent coordination."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .._types import CollaborationResult, HarnessEvent
from ..events import EventBus
from ...harness.agents.base import BaseAgent


class BaseCollaborationPattern(ABC):
    """Abstract base for multi-agent collaboration patterns.

    Each pattern defines how agents interact: order, turns, voting, etc.
    """

    name: str
    agents: list[BaseAgent]
    event_bus: EventBus

    def __init__(
        self,
        name: str,
        agents: list[BaseAgent],
        event_bus: EventBus,
    ) -> None:
        self.name = name
        self.agents = agents
        self.event_bus = event_bus

    @abstractmethod
    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        ...

    def _emit(self, action: str, payload: dict[str, Any] | None = None) -> None:
        self.event_bus.emit(
            HarnessEvent(
                layer="collaboration",
                component=self.name,
                action=action,
                payload=payload or {},
            )
        )
