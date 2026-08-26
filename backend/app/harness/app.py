"""AppHarness — Harness 框架的顶层生命周期管理器。

这是整个多 Agent 系统的"总入口"和"装配中心"。
它按依赖顺序创建并组装所有 harness 组件：
    Storage → TaskBroker → SkillRegistry → ToolRegistry →
    AgentFactory → HITLManager → CollaborationRegistry → PaperPipelineOrchestrator

使用方式：
    harness = get_app_harness()   # 获取全局单例
    await harness.startup()       # 初始化所有组件
    # ... 使用 harness.agent_factory / harness.pipeline_orchestrator 等 ...
    await harness.shutdown()      # 关闭清理
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from ..agent.bootstrap import build_runtime
from ..agent.worker import start_embedded_worker
from ..agent_skills import SkillRegistry
from ..pipeline.state_broker import TaskBroker
from ..storage import Storage
from ._types import HarnessEvent
from .agents.factory import AgentFactory
from .collaboration.registry import CollaborationRegistry
from .config import HarnessSettings
from .events import EventBus
from .hitl.base import HITLManager
from .hitl.store import HITLStore
from .pipeline.orchestrator import PaperPipelineOrchestrator
from .tools.base import HarnessToolRegistry
from .tools.rate_limiter import RateLimiter


class AppHarness:
    """中央生命周期管理器，拥有并装配所有 harness 组件。

    替代了原来 dependencies.py 中零散的单例创建方式，
    将所有组件的初始化集中在一处，确保依赖顺序正确。

    Attributes:
        settings: Harness 配置实例。
        event_bus: 进程内事件总线，所有组件共享。
        storage: 文件存储服务（论文、模板等）。
        broker: 任务状态分发器（前后端进度同步）。
        skill_registry: Agent 技能注册中心（向量检索选工具）。
        tool_harness: 工具执行包装器（带事件追踪和限流）。
        agent_factory: Agent 工厂（按角色创建 Agent 实例）。
        hitl_manager: 人机协同管理器（流水线暂停/恢复）。
        collaboration_registry: 多 Agent 协作模式注册中心。
        runtime: AgentLoop 运行时（ToolRegistry / Sandbox / StreamBus）。
        pipeline_orchestrator: 论文流水线编排器（主路径）。
        agent_worker: 嵌入式后台 worker（all-in-one 模式）。
    """

    def __init__(
        self,
        storage: Storage | None = None,
        broker: TaskBroker | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.settings = HarnessSettings()
        self.event_bus = EventBus()
        self._initialized = False  # 是否已完成初始化

        # 可选注入的外部单例（来自 dependencies.py，避免重复创建第二/第三套实例）。
        # 为 None 时由 startup() 自行构建（仅用于独立构造 AppHarness 的场景）。
        self.storage: Storage | None = storage
        self.broker: TaskBroker | None = broker
        self.skill_registry: SkillRegistry | None = skill_registry
        # 以下组件在 startup() 中按依赖顺序创建
        self.tool_harness: HarnessToolRegistry | None = None
        self.agent_factory: AgentFactory | None = None
        self.hitl_manager: HITLManager | None = None
        self.collaboration_registry: CollaborationRegistry | None = None
        self.runtime = None
        self.pipeline_orchestrator: PaperPipelineOrchestrator | None = None
        self.agent_worker = None

    async def startup(self) -> None:
        """按依赖顺序初始化所有组件。

        初始化顺序（后依赖前）：
        1. 核心服务：Storage（文件存储）、TaskBroker（任务分发）
        2. 技能注册：SkillRegistry（加载 Agent 可用工具/技能）
        3. Harness 包装层：HarnessToolRegistry、AgentFactory
        4. 人机协同：HITLStore + HITLManager
        5. 协作：CollaborationRegistry
        6. Agent 运行时 + PaperPipelineOrchestrator

        重复调用是安全的（幂等），只会在首次调用时实际初始化。
        """
        if self._initialized:
            return  # 防止重复初始化

        # 计算项目路径
        backend_root = Path(__file__).resolve().parents[2]  # backend/ 目录
        app_root = Path(__file__).resolve().parents[1]  # backend/app/ 目录

        # ── 1. 核心服务 ──
        # 优先复用外部注入的单例（dependencies.py 已构造，且 Storage 已挂载 OSS）；
        # 仅在未注入时才自建（独立构造 AppHarness 的场景）。
        if self.storage is None:
            self.storage = Storage(
                base_dir=backend_root / self.settings.data_dir,
                templates_dir=backend_root / self.settings.templates_dir,
                vector_provider=self.settings.vector_store_provider,
                vector_collection_name=self.settings.vector_collection_name,
                vector_db_subdir=self.settings.vector_db_subdir,
                embedding_model_name=self.settings.embedding_model_name,
                vector_distance_metric=self.settings.vector_distance_metric,
            )
        if self.broker is None:
            self.broker = TaskBroker()

        # ── 2. 技能注册中心 ──
        if self.skill_registry is None:
            self.skill_registry = SkillRegistry(
                skills_root=app_root / self.settings.agent_skills_dir,
                vector_db_dir=backend_root / self.settings.data_dir / self.settings.vector_db_subdir,
                embedding_model_name=self.settings.embedding_model_name,
                provider=self.settings.vector_store_provider,
                collection_name=self.settings.skills_collection_name,
            )
            # 自建时才立即加载；注入的单例由 dependencies.get_skill_registry() 懒加载，
            # 避免在启动阶段阻塞 torch / embedding 模型。
            self.skill_registry.load()

        # ── 3. Harness 包装层 ──
        # 工具注册器：包装 SkillRegistry，增加执行前后的事件追踪与限流
        rate_limiter = None
        if int(self.settings.tool_rate_limit_max_calls) > 0:
            rate_limiter = RateLimiter(
                max_calls=int(self.settings.tool_rate_limit_max_calls),
                window_seconds=float(self.settings.tool_rate_limit_window),
            )
        self.tool_harness = HarnessToolRegistry(self.skill_registry, self.event_bus, rate_limiter=rate_limiter)
        # Agent 工厂：按角色创建和管理 Agent 实例
        self.agent_factory = AgentFactory(self.event_bus, self.settings)

        # ── 4. 人机协同（Human-in-the-Loop） ──
        hitl_store = HITLStore(data_dir=backend_root / self.settings.data_dir / "hitl")
        self.hitl_manager = HITLManager(
            event_bus=self.event_bus,
            store=hitl_store,
            checkpoints=self.settings.hitl_checkpoints,
            poll_interval=self.settings.hitl_poll_interval,
        )

        # ── 5. 多 Agent 协作模式注册 ──
        self.collaboration_registry = CollaborationRegistry(self.event_bus)

        # ── 6. Agent 运行时（ReAct loop + ToolRegistry + Sandbox）──
        self.runtime = build_runtime(
            storage=self.storage,
            broker=self.broker,
            skill_registry=self.skill_registry,
            agent_factory=self.agent_factory,
            collaboration_registry=self.collaboration_registry,
            user_settings=self.settings,
        )
        self.pipeline_orchestrator = PaperPipelineOrchestrator(self.runtime)

        # ── 7. 嵌入式 Agent Worker（api / all-in-one 模式）──
        role = getattr(self.settings, "agent_service_role", "all-in-one")
        if role in ("all-in-one", "api", "worker"):
            self.agent_worker = await start_embedded_worker()

        self._initialized = True
        self.event_bus.emit(
            HarnessEvent(layer="app", component="AppHarness", action="started"),
        )

    async def shutdown(self) -> None:
        """关闭并清理所有资源。

        发射 stopping 事件通知订阅者，关闭 agent 持有的底层连接（OpenAI httpx 连接池），
        标记为未初始化状态。
        """
        self.event_bus.emit(
            HarnessEvent(layer="app", component="AppHarness", action="stopping"),
        )
        if self.agent_worker is not None:
            await self.agent_worker.stop()
            self.agent_worker = None
        # 关闭启动期 agent_factory 缓存的 agent 连接，避免 ResourceWarning
        if self.agent_factory is not None:
            for agent in getattr(self.agent_factory, "_agents", {}).values():
                with contextlib.suppress(Exception):
                    await agent.aclose()
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """是否已完成初始化。"""
        return self._initialized


# ── 模块级单例 ──
_instance: AppHarness | None = None


def get_app_harness(
    storage: Storage | None = None,
    broker: TaskBroker | None = None,
    skill_registry: SkillRegistry | None = None,
) -> AppHarness:
    """获取全局 AppHarness 单例。

    首次调用时创建实例（可注入 storage/broker/skill_registry 单例，避免重复创建），
    后续调用返回同一实例。注意：此函数只创建实例，不执行 startup()，
    startup() 需要在 FastAPI lifespan 中显式调用。

    Returns:
        AppHarness: 全局唯一的 AppHarness 实例。
    """
    global _instance
    if _instance is None:
        _instance = AppHarness(storage=storage, broker=broker, skill_registry=skill_registry)
    return _instance
