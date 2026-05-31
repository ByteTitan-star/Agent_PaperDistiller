"""LangGraph adapter — wraps existing build_pipeline_graph / graph.ainvoke."""

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
    """Wraps the existing LangGraph pipeline with trace spans and events."""

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

    async def run(self, initial_state: dict[str, Any], tracer: Tracer, settings: HarnessSettings | None = None) -> list[str]:
        """Execute the LangGraph pipeline, wrapped in a trace span."""
        effective_settings = settings or self.settings
        tracer.start_span("langgraph_pipeline")

        graph = build_pipeline_graph(
            storage=self.storage,
            broker=self.broker,
            settings=effective_settings,
        )
        if graph is None:
            tracer.end_span("skipped", {"reason": "LangGraph not available"})
            raise RuntimeError("LangGraph not available")

        self.event_bus.emit(
            HarnessEvent(layer="pipeline", component="langgraph_adapter", action="start"),
        )

        try:
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
