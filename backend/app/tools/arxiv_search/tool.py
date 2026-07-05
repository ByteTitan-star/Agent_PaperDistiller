"""arXiv search tool."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


class ArxivSearchTool(Tool):
    name: ClassVar[str] = "arxiv_search_tool"
    description: ClassVar[str] = "Search arXiv for academic papers by keyword."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "default": 5},
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
            "arxiv_search_tool",
            {"query": args.get("query", ""), "max_results": int(args.get("max_results") or 5)},
        )
        if "error" in result:
            return ToolResult(ToolResultStatus.ERROR, result["error"])
        return ToolResult(ToolResultStatus.SUCCESS, json.dumps(result, ensure_ascii=False)[:8000])


tool = ArxivSearchTool()
