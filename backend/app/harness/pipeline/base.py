"""PipelineHarness — orchestrates pipeline execution with hooks and tracing."""

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


HookCallback = Callable[[HarnessEvent], None]


class PipelineHarness:
    """Top-level pipeline orchestrator with trace spans, events, and HITL.

    Wraps the existing ``run_pipeline()`` function. Emits events for
    before_step / after_step / on_error / on_complete hooks.
    """

    def __init__(
        self,
        storage: Storage,
        broker: TaskBroker,
        settings: HarnessSettings,
        event_bus: EventBus,
        hitl_manager: HITLManager | None = None,
    ) -> None:
        self.storage = storage
        self.broker = broker
        self.settings = settings
        self.event_bus = event_bus
        self.hitl_manager = hitl_manager

        self.langgraph_adapter = LangGraphAdapter(storage, broker, settings, event_bus)
        self.linear_adapter = LinearAdapter(storage, broker, settings, event_bus)

        self._hooks: dict[str, list[HookCallback]] = {
            "before_step": [],
            "after_step": [],
            "on_error": [],
            "on_complete": [],
        }

    def on(self, event: str, callback: HookCallback) -> None:
        if event in self._hooks:
            self._hooks[event].append(callback)

    async def run(
        self,
        task_id: str,
        paper_id: str,
        title: str,
        target_language: str,
        template_name: str,
    ) -> list[str]:
        """Execute the pipeline, choosing LangGraph or linear adapter."""
        tracer = Tracer()
        self._emit("before_step", {"task_id": task_id, "paper_id": paper_id})

        # HITL: pre-pipeline check
        if self.hitl_manager and self.hitl_manager.has_checkpoint("pipeline_start"):
            from ..hitl.base import HITLDecision
            state_snapshot = {
                "task_id": task_id, "paper_id": paper_id, "title": title,
                "target_language": target_language, "template_name": template_name,
            }
            decision = await self.hitl_manager.check("pipeline_start", state_snapshot)
            if decision.action == "rejected":
                self._emit("on_error", {"task_id": task_id, "reason": "rejected by human"})
                return []

        try:
            tags: list[str] = []

            if self.settings.langgraph_enabled:
                try:
                    initial_state = {
                        "task_id": task_id,
                        "paper_id": paper_id,
                        "title": title,
                        "target_language": target_language,
                        "template_name": template_name,
                        "generation_model_name": self.settings.generation_model_name,
                        "evaluation_model_name": self.settings.evaluation_model_name,
                    }
                    tags = await self.langgraph_adapter.run(initial_state, tracer)
                except Exception:
                    # Fallback to linear
                    tags = await self.linear_adapter.run(
                        task_id, paper_id, title, target_language, template_name, tracer,
                    )
            else:
                tags = await self.linear_adapter.run(
                    task_id, paper_id, title, target_language, template_name, tracer,
                )

            self._emit("on_complete", {"task_id": task_id, "tags": tags})
            return tags
        except Exception as exc:
            self._emit("on_error", {"task_id": task_id, "error": str(exc)})
            raise

    def _emit(self, action: str, payload: dict[str, Any] | None = None) -> None:
        event = HarnessEvent(
            layer="pipeline",
            component="PipelineHarness",
            action=action,
            payload=payload or {},
        )
        self.event_bus.emit(event)
        for callback in self._hooks.get(action, []):
            try:
                callback(event)
            except Exception:
                pass
