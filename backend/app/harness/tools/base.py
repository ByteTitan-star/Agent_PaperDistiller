"""HarnessToolRegistry — wraps SkillRegistry with hooks and tracking."""

from __future__ import annotations

from typing import Any

from .._types import HarnessEvent
from ..events import EventBus
from ...agent_skills import LoadedSkill, SkillRegistry


class HarnessToolRegistry:
    """Delegation wrapper around existing SkillRegistry.

    Adds pre/post execution hooks, usage tracking, and event emission
    while delegating all real work to the underlying SkillRegistry.
    """

    def __init__(self, inner: SkillRegistry, event_bus: EventBus) -> None:
        self._inner = inner
        self.event_bus = event_bus
        self._call_count: dict[str, int] = {}

    # --- pass-through delegation ---

    def load(self) -> int:
        return self._inner.load()

    def status(self) -> dict[str, Any]:
        return self._inner.status()

    def all_tools(self) -> list[LoadedSkill]:
        return self._inner.all_tools()

    def select_tools(self, query: str, top_k: int, min_similarity: float = 0.8) -> list[LoadedSkill]:
        return self._inner.select_tools(query, top_k, min_similarity)

    def build_openai_tools(self, selected_skills: list[LoadedSkill]) -> list[dict[str, Any]]:
        return self._inner.build_openai_tools(selected_skills)

    def build_skill_hint(self, selected_skills: list[LoadedSkill]) -> str:
        return self._inner.build_skill_hint(selected_skills)

    # --- wrapped execute with hooks ---

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.event_bus.emit(
            HarnessEvent(
                layer="tool",
                component=tool_name,
                action="pre_execute",
                payload={"arguments_keys": list(arguments.keys())},
            )
        )

        result = self._inner.execute(tool_name, arguments, context)

        self._call_count[tool_name] = self._call_count.get(tool_name, 0) + 1
        has_error = "error" in result
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
        return dict(self._call_count)
