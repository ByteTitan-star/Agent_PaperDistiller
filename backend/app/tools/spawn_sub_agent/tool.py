"""Spawn an isolated sub-agent session."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


class SpawnSubAgentTool(Tool):
    name: ClassVar[str] = "spawn_sub_agent"
    description: ClassVar[str] = (
        "Delegate a sub-task to a specialized sub-agent with its own tool set. "
        "Batch multiple spawn calls in one message for parallel work. "
        "Set wait=inline to block until done, fanin to collect later, never to fire-and-forget."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Clear task description for the sub-agent"},
            "system_prompt": {"type": "string", "description": "Optional persona override"},
            "tool_mask": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowlist of tool names",
            },
            "wait": {"type": "string", "enum": ["inline", "fanin", "never"], "default": "inline"},
        },
        "required": ["task"],
    }
    concurrency_safe: ClassVar[bool] = True

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        runtime = context.runtime
        if runtime is None or runtime.loop is None:
            return ToolResult(ToolResultStatus.ERROR, "Agent loop unavailable")

        mask = args.get("tool_mask")
        tool_mask = set(mask) if isinstance(mask, list) else None
        wait = args.get("wait") or "inline"
        handle = await runtime.loop.spawn_sub_agent(
            parent_session_id=context.session_id,
            auth=context.auth,
            task=args.get("task") or "",
            system_prompt=args.get("system_prompt"),
            tool_mask=tool_mask,
            wait=wait,
        )
        payload = {
            "handle_id": handle.handle_id,
            "child_session_id": handle.child_session_id,
            "status": handle.status,
            "result": handle.result,
            "error": handle.error,
        }
        status = ToolResultStatus.SUCCESS if handle.status != "error" else ToolResultStatus.ERROR
        return ToolResult(status, json.dumps(payload, ensure_ascii=False))


tool = SpawnSubAgentTool()
