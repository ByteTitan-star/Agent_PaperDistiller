"""HarnessToolRegistry — 包装 SkillRegistry，增加事件追踪和调用统计。

不改变 SkillRegistry 的任何核心逻辑，只是在 execute() 调用前后：
- 发射 pre_execute / post_execute 事件到 EventBus
- 累计调用次数（按工具名统计）
"""

from __future__ import annotations

from typing import Any

from .._types import HarnessEvent
from ..events import EventBus
from ...agent_skills import LoadedSkill, SkillRegistry
from .rate_limiter import RateLimiter


class HarnessToolRegistry:
    """SkillRegistry 的委托包装器。

    设计模式：装饰器模式（Decorator Pattern）
    - 所有读操作（load, status, all_tools, select_tools 等）直接透传给内部 SkillRegistry
    - execute() 调用被拦截，增加事件发射、调用计数、限流

    Attributes:
        _inner: 被包装的 SkillRegistry 实例。
        event_bus: 事件总线。
        _call_count: 各工具的调用次数统计 {"tool_name": count}。
        _rate_limiter: 可选的滑动窗口限流器（None 表示不限流）。
    """

    def __init__(self, inner: SkillRegistry, event_bus: EventBus, rate_limiter: RateLimiter | None = None) -> None:
        """初始化工具注册器。

        Args:
            inner: 底层的 SkillRegistry 实例。
            event_bus: 事件总线。
            rate_limiter: 可选的工具限流器；为 None 时不限流。
        """
        self._inner = inner
        self.event_bus = event_bus
        self._call_count: dict[str, int] = {}
        self._rate_limiter = rate_limiter

    # ---- 直接透传的方法（不增加任何逻辑） ----

    def load(self) -> int:
        """加载所有技能定义（透传给 SkillRegistry）。

        Returns:
            int: 加载的技能数量。
        """
        return self._inner.load()

    def status(self) -> dict[str, Any]:
        """获取技能注册状态（透传给 SkillRegistry）。

        Returns:
            dict: 状态信息字典。
        """
        return self._inner.status()

    def all_tools(self) -> list[LoadedSkill]:
        """获取所有已加载的技能列表（透传给 SkillRegistry）。

        Returns:
            list[LoadedSkill]: 技能列表。
        """
        return self._inner.all_tools()

    def select_tools(self, query: str, top_k: int, min_similarity: float = 0.8) -> list[LoadedSkill]:
        """通过向量相似度选择与 query 相关的技能（透传给 SkillRegistry）。

        Args:
            query: 查询文本。
            top_k: 返回的最大数量。
            min_similarity: 最低相似度阈值。

        Returns:
            list[LoadedSkill]: 匹配的技能列表。
        """
        return self._inner.select_tools(query, top_k, min_similarity)

    def build_openai_tools(self, selected_skills: list[LoadedSkill]) -> list[dict[str, Any]]:
        """将技能转换为 OpenAI Function Calling 格式（透传给 SkillRegistry）。

        Args:
            selected_skills: 选中的技能列表。

        Returns:
            list[dict]: OpenAI tools 格式的列表。
        """
        return self._inner.build_openai_tools(selected_skills)

    def build_skill_hint(self, selected_skills: list[LoadedSkill]) -> str:
        """生成技能提示文本（透传给 SkillRegistry）。

        Args:
            selected_skills: 选中的技能列表。

        Returns:
            str: 技能提示文本。
        """
        return self._inner.build_skill_hint(selected_skills)

    # ---- 包装了事件追踪的方法 ----

    def execute(
        self,
        tool_name: str,                           # 工具名称
        arguments: dict[str, Any],                 # 工具参数
        context: dict[str, Any] | None = None,     # 执行上下文（可选）
    ) -> dict[str, Any]:
        """执行工具调用，前后发射事件并统计调用次数。

        流程：
        1. 发射 pre_execute 事件
        2. 调用底层 SkillRegistry.execute()
        3. 累加调用计数
        4. 发射 post_execute 事件（含是否错误和调用次数）

        Args:
            tool_name: 工具名称。
            arguments: 工具调用参数字典。
            context: 执行上下文（可选）。

        Returns:
            dict: 工具执行结果。
        """
        # 执行前事件
        self.event_bus.emit(
            HarnessEvent(
                layer="tool",
                component=tool_name,
                action="pre_execute",
                payload={"arguments_keys": list(arguments.keys())},
            )
        )

        # 限流：超出窗口内最大调用次数则直接拒绝（保护如 Tavily 等外部配额）
        if self._rate_limiter is not None and not self._rate_limiter.allow(tool_name):
            self.event_bus.emit(
                HarnessEvent(
                    layer="tool",
                    component=tool_name,
                    action="rate_limited",
                )
            )
            return {"error": "rate limited"}

        # 执行实际调用
        result = self._inner.execute(tool_name, arguments, context)

        # 累加调用计数
        self._call_count[tool_name] = self._call_count.get(tool_name, 0) + 1
        has_error = "error" in result

        # 执行后事件
        self.event_bus.emit(
            HarnessEvent(
                layer="tool",
                component=tool_name,
                action="post_execute",
                payload={"has_error": has_error, "call_count": self._call_count[tool_name]},
            )
        )

        return result

    def usage_stats(self) -> dict[str, int]:
        """获取各工具的调用次数统计。

        Returns:
            dict: 工具名到调用次数的映射 {"web_search": 5, "code_search": 2, ...}。
        """
        return dict(self._call_count)
