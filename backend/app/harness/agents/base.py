"""BaseAgent — template-method lifecycle for all agents."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from .._types import AgentResult, AgentRole, HarnessEvent, LifecyclePhase, TokenUsage
from ..config import HarnessSettings
from ..events import EventBus


class BaseAgent(ABC):
    """Abstract base for all harness-managed agents.

    Subclasses implement ``_do_run``; the public ``execute`` method
    guarantees the lifecycle hook order:
        on_init → on_pre_run → _do_run → on_post_run
    with on_error called on any exception.
    """

    name: str
    role: AgentRole
    event_bus: EventBus
    settings: HarnessSettings

    def __init__(
        self,
        name: str,
        role: AgentRole,
        event_bus: EventBus,
        settings: HarnessSettings,
    ) -> None:
        self.name = name
        self.role = role
        self.event_bus = event_bus
        self.settings = settings

    async def execute(self, prompt: str, **kwargs: object) -> AgentResult:
        """Template method — runs lifecycle hooks around ``_do_run``."""
        self.on_init()
        self.event_bus.emit(
            HarnessEvent(layer="agent", component=self.name, action="init"),
        )
        try:
            prepared = self.on_pre_run(prompt, **kwargs)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="pre_run"),
            )
            result = await self._do_run(prepared, **kwargs)
            self.on_post_run(result)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="post_run",
                            payload={"has_error": result.error is not None}),
            )
            return result
        except Exception as exc:
            self.on_error(exc)
            self.event_bus.emit(
                HarnessEvent(layer="agent", component=self.name, action="error",
                            payload={"error": str(exc)}),
            )
            return AgentResult(error=str(exc))

    @abstractmethod
    async def _do_run(self, prompt: str, **kwargs: object) -> AgentResult:
        """Subclass implements actual agent logic here."""
        ...

    # --- overridable hooks ---

    def on_init(self) -> None:
        pass

    def on_pre_run(self, prompt: str, **kwargs: object) -> str:
        return prompt

    def on_post_run(self, result: AgentResult) -> None:
        pass

    def on_error(self, error: Exception) -> None:
        pass
