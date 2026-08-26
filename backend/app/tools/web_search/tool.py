"""Web search tool — Tavily-backed."""

from __future__ import annotations

from typing import Any, ClassVar

from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


class WebSearchTool(Tool):
    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the internet for recent papers, code, datasets, news, and technical blogs. "
        "Use when the question needs up-to-date or external information."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search keywords"},
            "max_results": {"type": "integer", "description": "Max results", "default": 3},
        },
        "required": ["query"],
    }
    concurrency_safe: ClassVar[bool] = True
    deduplicate_repeated_calls: ClassVar[bool] = True

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        runtime = context.runtime
        if runtime is None or runtime.skill_registry is None:
            return ToolResult(ToolResultStatus.ERROR, "Skill registry unavailable")
        result = runtime.skill_registry.execute(
            "web_search",
            {"query": args.get("query", ""), "max_results": int(args.get("max_results") or 3)},
        )
        if "error" in result:
            return ToolResult(ToolResultStatus.ERROR, result["error"])
        items = result.get("results") or []
        if not items:
            return ToolResult(ToolResultStatus.SUCCESS, "No results found.")
        parts = []
        for i, item in enumerate(items, 1):
            title = item.get("title", "untitled")
            url = item.get("url", "")
            content = (item.get("content") or "")[:400]
            parts.append(f"{i}. [{title}]({url})\n{content}")
        return ToolResult(ToolResultStatus.SUCCESS, "\n\n".join(parts))


tool = WebSearchTool()
