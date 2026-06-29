"""线性适配器 — 封装 run_pipeline_linear 顺序执行。

将现有的 run_pipeline_linear 函数包装到 harness 追踪和事件系统中，
作为 LangGraph 不可用时的兼容执行模式。
"""

from __future__ import annotations

from typing import Any

from .._types import HarnessEvent
from ..config import HarnessSettings
from ..events import EventBus
from ...pipeline.state_broker import TaskBroker
from ...pipeline.workflow_graph import run_pipeline_linear
from ...storage import Storage
from .tracing import Tracer


class LinearAdapter:
    """线性流水线适配器，在追踪和事件系统中包装顺序执行。

    当配置中 langgraph_enabled=False，或 LangGraph 执行失败降级时，
    PipelineHarness 使用此适配器。

    线性模式按固定顺序依次执行各步骤（解析 → 翻译 → 提取 → ...），
    不使用 LangGraph 的状态图和条件路由。

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
        task_id: str,            # 任务 ID
        paper_id: str,           # 论文 ID
        title: str,              # 论文标题
        target_language: str,    # 目标语言
        template_name: str,      # 模板名称
        tracer: Tracer,          # 追踪器
        settings: HarnessSettings | None = None,  # 可覆盖配置
        agent_factory: Any = None,                # harness Agent 工厂
    ) -> list[str]:
        """执行线性流水线，包装在追踪跨度中。

        流程：
        1. 开始追踪跨度 "linear_pipeline"
        2. 调用 run_pipeline_linear 顺序执行
        3. 提取结果中的 tags 列表
        4. 结束追踪跨度

        Args:
            task_id: 任务唯一标识。
            paper_id: 论文唯一标识。
            title: 论文标题。
            target_language: 目标翻译语言。
            template_name: 分析模板名称。
            tracer: 追踪器实例。
            settings: 可选的配置覆盖。

        Returns:
            list[str]: 提取到的标签列表。
        """
        effective_settings = settings or self.settings
        tracer.start_span("linear_pipeline")

        if agent_factory is None:
            raise RuntimeError("agent_factory is required to route pipeline LLM calls through harness agents")

        self.event_bus.emit(
            HarnessEvent(layer="pipeline", component="linear_adapter", action="start"),
        )

        try:
            # 调用现有的线性流水线函数
            tags = await run_pipeline_linear(
                task_id=task_id,
                paper_id=paper_id,
                title=title,
                target_language=target_language,
                template_name=template_name,
                storage=self.storage,
                broker=self.broker,
                settings=effective_settings,
                agent_factory=agent_factory,
            )
            tracer.end_span("ok", {"tags": tags})
            self.event_bus.emit(
                HarnessEvent(layer="pipeline", component="linear_adapter", action="complete"),
            )
            return tags
        except Exception as exc:
            tracer.end_span("error", {"error": str(exc)})
            self.event_bus.emit(
                HarnessEvent(layer="pipeline", component="linear_adapter", action="error",
                            payload={"error": str(exc)}),
            )
            raise
