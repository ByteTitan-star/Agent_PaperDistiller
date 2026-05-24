"""AppHarness — top-level lifecycle manager for all harness components."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._types import HarnessEvent
from .agents.factory import AgentFactory
from .collaboration.registry import CollaborationRegistry
from .config import HarnessSettings
from .events import EventBus
from .hitl.base import HITLManager
from .hitl.store import HITLStore
from .pipeline.base import PipelineHarness
from .session.base import SessionManager
from .session.chat_adapter import ChatAdapter
from .tools.base import HarnessToolRegistry

from ..agent_skills import SkillRegistry
from ..pipeline.state_broker import TaskBroker
from ..storage import Storage


class AppHarness:
    """Central lifecycle manager. Owns and wires all harness components.

    Replaces the ad-hoc singleton creation in ``dependencies.py``.
    """

    def __init__(self) -> None:
        self.settings = HarnessSettings()
        self.event_bus = EventBus()
        self._initialized = False

        # Populated during startup()
        self.storage: Storage | None = None
        self.broker: TaskBroker | None = None
        self.skill_registry: SkillRegistry | None = None
        self.tool_harness: HarnessToolRegistry | None = None
        self.agent_factory: AgentFactory | None = None
        self.hitl_manager: HITLManager | None = None
        self.pipeline_harness: PipelineHarness | None = None
        self.session_manager: SessionManager | None = None
        self.chat_adapter: ChatAdapter | None = None
        self.collaboration_registry: CollaborationRegistry | None = None

    async def startup(self) -> None:
        """Initialize all components in dependency order."""
        if self._initialized:
            return

        backend_root = Path(__file__).resolve().parents[2]
        project_root = backend_root.parent

        # 1. Core services (existing)
        self.storage = Storage(
            base_dir=backend_root / self.settings.data_dir,
            templates_dir=backend_root / self.settings.templates_dir,
            vector_provider=self.settings.vector_store_provider,
            vector_collection_name=self.settings.vector_collection_name,
            vector_db_subdir=self.settings.vector_db_subdir,
            embedding_model_name=self.settings.embedding_model_name,
            vector_distance_metric=self.settings.vector_distance_metric,
        )
        self.broker = TaskBroker()

        # 2. Skill registry (existing)
        self.skill_registry = SkillRegistry(
            skills_root=project_root / self.settings.agent_skills_dir,
            vector_db_dir=backend_root / self.settings.data_dir / self.settings.vector_db_subdir,
            embedding_model_name=self.settings.embedding_model_name,
            provider=self.settings.vector_store_provider,
            collection_name=self.settings.skills_collection_name,
        )
        self.skill_registry.load()

        # 3. Harness wrappers
        self.tool_harness = HarnessToolRegistry(self.skill_registry, self.event_bus)
        self.agent_factory = AgentFactory(self.event_bus, self.settings)

        # 4. HITL
        hitl_store = HITLStore(data_dir=backend_root / self.settings.data_dir / "hitl")
        self.hitl_manager = HITLManager(
            event_bus=self.event_bus,
            store=hitl_store,
            checkpoints=self.settings.hitl_checkpoints,
            poll_interval=self.settings.hitl_poll_interval,
        )

        # 5. Pipeline
        self.pipeline_harness = PipelineHarness(
            storage=self.storage,
            broker=self.broker,
            settings=self.settings,
            event_bus=self.event_bus,
            hitl_manager=self.hitl_manager,
        )

        # 6. Session
        self.session_manager = SessionManager(self.event_bus)
        self.chat_adapter = ChatAdapter(
            session_manager=self.session_manager,
            storage=self.storage,
            event_bus=self.event_bus,
        )

        # 7. Collaboration
        self.collaboration_registry = CollaborationRegistry(self.event_bus)

        self._initialized = True
        self.event_bus.emit(
            HarnessEvent(layer="app", component="AppHarness", action="started"),
        )

    async def shutdown(self) -> None:
        """Clean up resources."""
        self.event_bus.emit(
            HarnessEvent(layer="app", component="AppHarness", action="stopping"),
        )
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized


# Module-level singleton
_instance: AppHarness | None = None


def get_app_harness() -> AppHarness:
    """Return the global AppHarness singleton."""
    global _instance
    if _instance is None:
        _instance = AppHarness()
    return _instance
