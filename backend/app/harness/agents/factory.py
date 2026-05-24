"""AgentFactory — config-driven agent creation."""

from __future__ import annotations

from .._types import AgentRole
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent
from .deepseek_agent import DeepSeekAgent
from .qwen_agent import QwenAgent
from .tot_agent import ToTAgent


class AgentFactory:
    """Creates agent instances from settings."""

    def __init__(self, event_bus: EventBus, settings: HarnessSettings) -> None:
        self.event_bus = event_bus
        self.settings = settings
        self._agents: dict[str, BaseAgent] = {}

    def create_deepseek(self) -> DeepSeekAgent:
        return DeepSeekAgent(event_bus=self.event_bus, settings=self.settings)

    def create_qwen(self) -> QwenAgent:
        return QwenAgent(event_bus=self.event_bus, settings=self.settings)

    def create_tot(self) -> ToTAgent:
        generator = self.create_deepseek()
        evaluator = self.create_qwen()
        return ToTAgent(
            generator=generator,
            evaluator=evaluator,
            event_bus=self.event_bus,
            settings=self.settings,
        )

    def get_or_create(self, role: AgentRole) -> BaseAgent:
        key = role.value
        if key in self._agents:
            return self._agents[key]

        if role == AgentRole.GENERATOR:
            agent = self.create_deepseek()
        elif role == AgentRole.EVALUATOR:
            agent = self.create_qwen()
        elif role == AgentRole.CRITIC:
            agent = self.create_tot()
        else:
            agent = self.create_deepseek()

        self._agents[key] = agent
        return agent
