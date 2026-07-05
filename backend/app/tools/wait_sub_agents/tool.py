"""Wait for spawned sub-agents to finish."""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


class WaitSubAgentsTool(Tool):
    name: ClassVar[str] = "wait_sub_agents"
    description: ClassVar[str] = "Collect results from sub-agents spawned with wait=fanin or wait=never."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "handle_ids": {"type": "array", "items": {"type": "string"}},
            "timeout_sec": {"type": "number", "default": 120},
        },
        "required": ["handle_ids"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        runtime = context.runtime
        if runtime is None or runtime.sub_agent_store is None:
            return ToolResult(ToolResultStatus.ERROR, "Sub-agent store unavailable")

        handle_ids = args.get("handle_ids") or []
        timeout = float(args.get("timeout_sec") or 120)
        deadline = asyncio.get_running_loop().time() + timeout
        results = []

        while asyncio.get_running_loop().time() < deadline:
            done = True
            for hid in handle_ids:
                rec = await runtime.sub_agent_store.get(hid)
                if rec is None:
                    results.append({"handle_id": hid, "status": "missing"})
                    continue
                if rec.status in ("pending", "running"):
                    done = False
                    continue
                results.append(
                    {
                        "handle_id": hid,
                        "status": rec.status,
                        "result": rec.result,
                        "error": rec.error,
                        "task": rec.task_summary,
                    }
                )
            if done:
                return ToolResult(ToolResultStatus.SUCCESS, json.dumps(results, ensure_ascii=False))
            await asyncio.sleep(0.5)

        return ToolResult(
            ToolResultStatus.ERROR,
            json.dumps({"partial": results, "error": "timeout"}, ensure_ascii=False),
        )


tool = WaitSubAgentsTool()
