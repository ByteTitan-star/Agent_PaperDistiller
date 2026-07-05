"""Agent 工厂 — 根据角色（AgentRole）创建对应的 Agent 实例。

集中管理 Agent 的创建逻辑，支持单例缓存（同一角色只创建一个实例），
避免重复创建导致的资源浪费。上层代码只需传入角色枚举，
工厂自动选择并实例化对应的 Agent。
"""

from __future__ import annotations

from .._types import AgentRole
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent
from .deepseek_agent import DeepSeekAgent
from .qwen_agent import QwenAgent
from .tot_agent import ToTAgent


class AgentFactory:
    """Agent 工厂，根据角色创建 Agent 实例。

    角色与 Agent 的映射关系：
    - GENERATOR → DeepSeekAgent（生成内容）
    - EVALUATOR → QwenAgent（评估质量）
    - CRITIC → ToTAgent（Tree-of-Thought 多分支批判）

    Attributes:
        event_bus: 事件总线，传递给每个创建的 Agent。
        settings: 框架配置，传递给每个创建的 Agent。
        _agents: 角色到 Agent 实例的缓存字典（单例模式）。
    """

    def __init__(self, event_bus: EventBus, settings: HarnessSettings) -> None:
        self.event_bus = event_bus
        self.settings = settings
        # 缓存已创建的 Agent，避免重复实例化
        self._agents: dict[str, BaseAgent] = {}

    def create_deepseek(self) -> DeepSeekAgent:
        """创建 DeepSeek Agent 实例（生成器角色）。

        Returns:
            DeepSeekAgent: 新的 DeepSeek Agent 实例。
        """
        return DeepSeekAgent(event_bus=self.event_bus, settings=self.settings)

    def create_qwen(self) -> QwenAgent:
        """创建 Qwen Agent 实例（评估器角色）。

        Returns:
            QwenAgent: 新的 Qwen Agent 实例。
        """
        return QwenAgent(event_bus=self.event_bus, settings=self.settings)

    def create_tot(self) -> ToTAgent:
        """创建 ToT Agent 实例（Tree-of-Thought 评论者角色）。

        ToT Agent 内部组合了 DeepSeek（生成分支）+ Qwen（评估分支），
        因此会同时创建这两个子 Agent。

        Returns:
            ToTAgent: 新的 ToT Agent 实例。
        """
        generator = self.create_deepseek()  # 生成分支候选
        evaluator = self.create_qwen()  # 评估和打分
        return ToTAgent(
            generator=generator,
            evaluator=evaluator,
            event_bus=self.event_bus,
            settings=self.settings,
        )

    def get_or_create(self, role: AgentRole) -> BaseAgent:
        """根据角色获取或创建 Agent（带缓存）。

        如果该角色已有缓存实例则直接返回，否则创建新实例并缓存。
        同一角色的 Agent 在整个生命周期中只创建一次。

        Args:
            role: Agent 角色枚举，决定创建哪种 Agent。

        Returns:
            BaseAgent: 对应角色的 Agent 实例。
        """
        key = role.value
        # 命中缓存则直接返回
        if key in self._agents:
            return self._agents[key]

        # 按角色创建对应的 Agent
        if role == AgentRole.GENERATOR:
            agent = self.create_deepseek()
        elif role == AgentRole.EVALUATOR:
            agent = self.create_qwen()
        elif role == AgentRole.CRITIC:
            agent = self.create_tot()
        elif role == AgentRole.SUPERVISOR:
            # 监督者角色：复用 DeepSeek 作为任务分解/合并的 LLM agent（见 SupervisorPattern）
            agent = self.create_deepseek()
        else:
            # TRANSLATOR / PARSER 故意不实现为 agent：
            #   - TRANSLATOR 走 Google 免费翻译（非 LLM，见 pipeline/translator.py）
            #   - PARSER 走 PyPDF 同步解析（非 LLM，见 pipeline/document_parser.py）
            raise ValueError(f"AgentRole {role} 没有 agent 实现（TRANSLATOR/PARSER 为非 LLM 同步步骤，不经过 agent）")

        # 缓存并返回
        self._agents[key] = agent
        return agent
