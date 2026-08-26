"""Run a registered skill script in-process (reviewed, trusted code)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


class RunSkillTool(Tool):
    name: ClassVar[str] = "run_skill"
    description: ClassVar[str] = "Execute a pre-registered skill by name with JSON arguments."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["skill_name"],
    }
    deduplicate_repeated_calls: ClassVar[bool] = True

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        runtime = context.runtime
        if runtime is None or runtime.skill_registry is None:
            return ToolResult(ToolResultStatus.ERROR, "Skill registry unavailable")
        name = args.get("skill_name") or ""
        skill_args = args.get("arguments") or {}
        if not isinstance(skill_args, dict):
            skill_args = {}
        result = runtime.skill_registry.execute(name, skill_args, context={"paper_id": context.paper_id})
        if "error" in result:
            return ToolResult(ToolResultStatus.ERROR, str(result["error"]))
        return ToolResult(ToolResultStatus.SUCCESS, json.dumps(result, ensure_ascii=False)[:8000])


tool = RunSkillTool()
