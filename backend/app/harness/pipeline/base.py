"""PipelineHarness — 流水线编排器，统一管理流水线执行、钩子、追踪和人机协同。

封装了 LangGraph 和线性两种流水线执行模式，提供：
- 执行前后钩子（before_step / after_step / on_error / on_complete）
- 自动选择 LangGraph 或线性适配器（LangGraph 失败自动降级为线性）
- HITL 审批检查点（流水线启动前可暂停等待人工决策）
- 追踪跨度记录（每步执行的时间和状态）
"""

from __future__ import annotations

from typing import Any, Callable

from .._types import HarnessEvent
from ..config import HarnessSettings
from ..events import EventBus
from ..hitl.base import HITLManager
from ...pipeline.state_broker import TaskBroker
from ...pipeline.workflow_graph import build_pipeline_graph
from ...storage import Storage
from .langgraph_adapter import LangGraphAdapter
from .linear_adapter import LinearAdapter
from .tracing import Tracer


# 钩子回调函数类型
HookCallback = Callable[[HarnessEvent], None]


class PipelineHarness:
    """顶层流水线编排器，集成追踪、事件和 HITL。

    封装了现有的 run_pipeline() 函数，在执行前后发射事件：
        before_step → [执行流水线] → on_complete
                                   → on_error（异常时）

    支持两种执行模式：
    - LangGraph 模式：通过 LangGraph 状态图执行（需要 langgraph 依赖）
    - 线性模式：顺序执行各步骤（兼容模式）

    Attributes:
        storage: 文件存储服务。
        broker: 任务状态分发器。
        settings: 框架配置。
        event_bus: 事件总线。
        hitl_manager: 人机协同管理器（可选）。
        langgraph_adapter: LangGraph 适配器。
        linear_adapter: 线性适配器。
    """

    def __init__(
        self,
        storage: Storage,                # 文件存储
        broker: TaskBroker,              # 任务分发器
        settings: HarnessSettings,       # 框架配置
        event_bus: EventBus,             # 事件总线
        hitl_manager: HITLManager | None = None,  # HITL 管理器（可选）
    ) -> None:
        self.storage = storage
        self.broker = broker
        self.settings = settings
        self.event_bus = event_bus
        self.hitl_manager = hitl_manager

        # 创建两种适配器
        self.langgraph_adapter = LangGraphAdapter(storage, broker, settings, event_bus)
        self.linear_adapter = LinearAdapter(storage, broker, settings, event_bus)

        # 用户注册的钩子回调
        self._hooks: dict[str, list[HookCallback]] = {
            "before_step": [],   # 流水线执行前
            "after_step": [],    # 每步执行后
            "on_error": [],      # 出错时
            "on_complete": [],   # 完成时
        }

    def on(self, event: str, callback: HookCallback) -> None:
        """注册钩子回调。

        Args:
            event: 事件名称，可选 "before_step" / "after_step" / "on_error" / "on_complete"。
            callback: 回调函数，接收 HarnessEvent 参数。
        """
        if event in self._hooks:
            self._hooks[event].append(callback)

    async def run(
        self,
        task_id: str,            # 任务 ID
        paper_id: str,           # 论文 ID
        title: str,              # 论文标题
        target_language: str,    # 目标语言
        template_name: str,      # 模板名称
        settings: HarnessSettings | None = None,  # 可覆盖配置
    ) -> list[str]:
        """执行流水线，自动选择 LangGraph 或线性模式。

        流程：
        1. 发射 before_step 事件
        2. HITL 检查：如果 pipeline_start 有检查点，暂停等待人工审批
        3. 根据配置选择 LangGraph 或线性模式执行
        4. LangGraph 模式失败时自动降级为线性模式
        5. 发射 on_complete 或 on_error 事件

        Args:
            task_id: 任务唯一标识。
            paper_id: 论文唯一标识。
            title: 论文标题。
            target_language: 目标翻译语言。
            template_name: 分析模板名称。
            settings: 可选的配置覆盖。

        Returns:
            list[str]: 提取到的标签列表。
        """
        effective_settings = settings or self.settings
        # 每个任务用用户的 per-task 配置新建 Agent 工厂，确保 harness agent 拿到正确的
        # API Key（修复此前"启动时 agent 用占位符 key、用户真实 key 到不了 agent"的致命 bug）。
        from ..agents.factory import AgentFactory
        agent_factory = AgentFactory(self.event_bus, effective_settings)
        tracer = Tracer()
        self._emit("before_step", {"task_id": task_id, "paper_id": paper_id})

        # ── HITL：流水线启动前检查（默认 hitl_checkpoints=[] 时不触发）──
        if self.hitl_manager and self.hitl_manager.has_checkpoint("pipeline_start"):
            state_snapshot = {
                "task_id": task_id, "paper_id": paper_id, "title": title,
                "target_language": target_language, "template_name": template_name,
            }
            hitl_state = await self.hitl_manager.interrupt("pipeline_start", state_snapshot)
            decision = await self.hitl_manager.wait_for_decision(hitl_state.id, timeout=3600.0)
            if decision.action == "rejected":
                # 人工拒绝，中止流水线
                self._emit("on_error", {"task_id": task_id, "reason": "rejected by human"})
                return []

        try:
            tags: list[str] = []

            if effective_settings.langgraph_enabled:
                try:
                    # 构建 LangGraph 初始状态
                    initial_state = {
                        "task_id": task_id,
                        "paper_id": paper_id,
                        "title": title,
                        "target_language": target_language,
                        "template_name": template_name,
                        "generation_model_name": effective_settings.generation_model_name,
                        "evaluation_model_name": effective_settings.evaluation_model_name,
                    }
                    tags = await self.langgraph_adapter.run(
                        initial_state, tracer, settings=effective_settings, agent_factory=agent_factory,
                    )
                except Exception:
                    # LangGraph 失败，降级为线性模式
                    tags = await self.linear_adapter.run(
                        task_id, paper_id, title, target_language, template_name, tracer,
                        settings=effective_settings, agent_factory=agent_factory,
                    )
            else:
                # 直接使用线性模式
                tags = await self.linear_adapter.run(
                    task_id, paper_id, title, target_language, template_name, tracer,
                    settings=effective_settings, agent_factory=agent_factory,
                )

            self._emit("on_complete", {"task_id": task_id, "tags": tags})
            return tags
        except Exception as exc:
            self._emit("on_error", {"task_id": task_id, "error": str(exc)})
            raise

    def _emit(self, action: str, payload: dict[str, Any] | None = None) -> None:
        """发射事件并同时调用注册的钩子回调。

        Args:
            action: 动作名称。
            payload: 附带数据。
        """
        event = HarnessEvent(
            layer="pipeline",
            component="PipelineHarness",
            action=action,
            payload=payload or {},
        )
        # 发射到事件总线
        self.event_bus.emit(event)
        # 调用注册的钩子回调
        for callback in self._hooks.get(action, []):
            try:
                callback(event)
            except Exception:
                pass
