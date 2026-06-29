"""协作模式注册中心 — 注册、创建和缓存多 Agent 协作模式实例。

内置两种协作模式：
- supervisor（监督者）：监督者分解任务 → 工人执行 → 监督者合并
- round_robin（轮询）：多个 Agent 轮流改进输出

（debate 辩论模式已移除——对抗式"提议者 vs 评论者"协作已由 ToTAgent 的
 generate→evaluate→prune 完整覆盖，且原 debate._adjudicate 是丢弃评审的残桩。）

支持通过 register_pattern() 注册自定义协作模式。
"""

from __future__ import annotations

from typing import Any

from ..events import EventBus
from ...harness.agents.base import BaseAgent
from .base import BaseCollaborationPattern
from .round_robin import RoundRobinPattern
from .supervisor import SupervisorPattern


# 内置协作模式映射：模式名 → 模式类
_BUILTIN_PATTERNS: dict[str, type[BaseCollaborationPattern]] = {
    "supervisor": SupervisorPattern,
    "round_robin": RoundRobinPattern,
}


class CollaborationRegistry:
    """协作模式注册中心，负责创建和缓存协作模式实例。

    按模式名和参与 Agent 列表作为缓存 key，
    相同组合只创建一个实例。

    Attributes:
        event_bus: 事件总线，传递给每个创建的协作模式。
        _patterns: 已创建的协作模式缓存，key 为 (mode, agent_names)。
        _custom: 用户注册的自定义协作模式类。
    """

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self._patterns: dict[str, BaseCollaborationPattern] = {}
        self._custom: dict[str, type[BaseCollaborationPattern]] = {}

    def register_pattern(self, name: str, cls: type[BaseCollaborationPattern]) -> None:
        """注册自定义协作模式。

        注册后可通过 create(name=自定义名) 创建实例。

        Args:
            name: 协作模式名称。
            cls: 协作模式类（必须继承 BaseCollaborationPattern）。
        """
        self._custom[name] = cls

    def create(
        self,
        mode: str,                    # 协作模式名称，如 "debate"
        agents: list[BaseAgent],       # 参与 Agent 列表
        **kwargs: Any,                 # 各模式的额外参数
    ) -> BaseCollaborationPattern:
        """创建或获取缓存的协作模式实例。

        根据模式名和 Agent 列表构建缓存 key，命中则直接返回。
        未命中则根据模式名创建新实例。

        Args:
            mode: 协作模式名称。
            agents: 参与协作的 Agent 列表。
            **kwargs: 各模式需要的额外参数：
                - supervisor: merge_prompt_template (str) 合并提示词模板
                - round_robin: rounds (int) 轮次, refinement_prompt (str) 改进提示词模板

        Returns:
            BaseCollaborationPattern: 协作模式实例。

        Raises:
            ValueError: 未知的协作模式名称。
        """
        # 构建缓存 key
        key = (mode, tuple(a.name for a in agents))
        if key in self._patterns:
            return self._patterns[key]

        # 查找模式类（自定义优先，然后内置）
        cls = self._custom.get(mode) or _BUILTIN_PATTERNS.get(mode)
        if cls is None:
            raise ValueError(f"Unknown collaboration pattern: {mode}")

        # 按模式创建实例，各模式构造函数参数不同
        if mode == "supervisor":
            pattern = SupervisorPattern(
                supervisor=agents[0],   # 监督者（第一个 Agent）
                workers=agents[1:],     # 工人（其余 Agent）
                event_bus=self.event_bus,
                merge_prompt_template=kwargs.get("merge_prompt_template"),  # 合并模板
            )
        elif mode == "round_robin":
            pattern = RoundRobinPattern(
                agents=agents,
                event_bus=self.event_bus,
                rounds=kwargs.get("rounds", 1),          # 轮询轮次
                refinement_prompt=kwargs.get("refinement_prompt"),  # 改进模板
            )
        else:
            # 自定义模式，使用通用构造
            pattern = cls(name=mode, agents=agents, event_bus=self.event_bus)

        # 缓存并返回
        self._patterns[key] = pattern
        return pattern
