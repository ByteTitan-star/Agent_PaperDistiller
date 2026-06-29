"""LangGraph 适配器 — 封装 LangGraph 状态图执行。

将现有的 build_pipeline_graph / graph.ainvoke 包装到 harness 追踪和事件系统中，
提供统一的 start → complete / error 事件发射。
"""

from __future__ import annotations

from typing import Any

from .._types import HarnessEvent
from ..config import HarnessSettings
from ..events import EventBus
from ...pipeline.state_broker import TaskBroker
from ...pipeline.workflow_graph import PaperState, build_pipeline_graph
from ...storage import Storage
from .tracing import Tracer


class LangGraphAdapter:
    """LangGraph 流水线适配器，在追踪和事件系统中包装 LangGraph 执行。

    当配置中 langgraph_enabled=True 时，PipelineHarness 使用此适配器。
    如果 LangGraph 不可用（缺少依赖），会抛出 RuntimeError，
    PipelineHarness 会自动降级为 LinearAdapter。

    Attributes:
        storage: 文件存储服务。
        broker: 任务状态分发器。
        settings: 框架配置。
        event_bus: 事件总线。
    """

    def __init__(
        self,
        storage: Storage,            # 文件存储
        broker: TaskBroker,          # 任务分发器
        settings: HarnessSettings,   # 框架配置
        event_bus: EventBus,         # 事件总线
    ) -> None:
        self.storage = storage
        self.broker = broker
        self.settings = settings
        self.event_bus = event_bus

    async def run(
        self,
        initial_state: dict[str, Any],       # LangGraph 初始状态
        tracer: Tracer,                       # 追踪器
        settings: HarnessSettings | None = None,  # 可覆盖配置
        agent_factory: Any = None,            # harness Agent 工厂（按角色创建/缓存 agent）
    ) -> list[str]:
        """执行 LangGraph 流水线，包装在追踪跨度中。

        流程：
        1. 开始追踪跨度 "langgraph_pipeline"
        2. 构建 LangGraph 状态图
        3. 执行 graph.ainvoke(initial_state)
        4. 提取结果中的 tags 列表
        5. 结束追踪跨度

        Args:
            initial_state: LangGraph 初始状态字典，包含 task_id、paper_id 等。
            tracer: 追踪器实例。
            settings: 可选的配置覆盖。

        Returns:
            list[str]: 提取到的标签列表。

        Raises:
            RuntimeError: LangGraph 不可用时抛出（触发 PipelineHarness 降级）。
        """
        effective_settings = settings or self.settings
        tracer.start_span("langgraph_pipeline")

        if agent_factory is None:
            raise RuntimeError("agent_factory is required to route pipeline LLM calls through harness agents")

        # 构建状态图
        graph = build_pipeline_graph(
            storage=self.storage,
            broker=self.broker,
            settings=effective_settings,
            agent_factory=agent_factory,
        )
        if graph is None:
            tracer.end_span("skipped", {"reason": "LangGraph not available"})
            raise RuntimeError("LangGraph not available")

        self.event_bus.emit(
            HarnessEvent(layer="pipeline", component="langgraph_adapter", action="start"),
        )

        try:
            # 执行 LangGraph 状态图
            result = await graph.ainvoke(initial_state)
            tags = list(result.get("tags", [])) if isinstance(result, dict) else []
            tracer.end_span("ok", {"tags": tags})
            self.event_bus.emit(
                HarnessEvent(layer="pipeline", component="langgraph_adapter", action="complete"),
            )
            return tags
        except Exception as exc:
            tracer.end_span("error", {"error": str(exc)})
            self.event_bus.emit(
                HarnessEvent(layer="pipeline", component="langgraph_adapter", action="error",
                            payload={"error": str(exc)}),
            )
            raise
