"""Linear adapter — wraps existing run_pipeline_linear."""

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
    """Wraps the existing linear pipeline with trace spans and events."""

    def __init__(
        self,
        storage: Storage,
        broker: TaskBroker,
        settings: HarnessSettings,
        event_bus: EventBus,
    ) -> None:
        self.storage = storage
        self.broker = broker
        self.settings = settings
        self.event_bus = event_bus

    async def run(
        self,
        task_id: str,
        paper_id: str,
        title: str,
        target_language: str,
        template_name: str,
        tracer: Tracer,
    ) -> list[str]:
        """Execute the linear pipeline, wrapped in a trace span."""
        tracer.start_span("linear_pipeline")

        self.event_bus.emit(
            HarnessEvent(layer="pipeline", component="linear_adapter", action="start"),
        )

        try:
            tags = await run_pipeline_linear(
                task_id=task_id,
                paper_id=paper_id,
                title=title,
                target_language=target_language,
                template_name=template_name,
                storage=self.storage,
                broker=self.broker,
                settings=self.settings,
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
