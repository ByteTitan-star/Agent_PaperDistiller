"""Pipeline step tools — paper distillation workflow as agent-callable primitives."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


def _require_runtime(context: ToolContext):
    if context.runtime is None:
        raise ValueError("Runtime unavailable")
    return context.runtime


class ParsePaperTool(Tool):
    name: ClassVar[str] = "parse_paper"
    description: ClassVar[str] = "Extract text and sections from the uploaded PDF for the current paper task."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"paper_id": {"type": "string"}, "task_id": {"type": "string"}},
        "required": ["paper_id", "task_id"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        from ...harness.pipeline.orchestrator import run_parse_step

        rt = _require_runtime(context)
        result = await run_parse_step(
            paper_id=args["paper_id"],
            task_id=args["task_id"],
            storage=rt.storage,
            broker=rt.broker,
            settings=rt.settings,
        )
        return ToolResult(ToolResultStatus.SUCCESS, json.dumps(result, ensure_ascii=False))


class TranslatePaperTool(Tool):
    name: ClassVar[str] = "translate_paper"
    description: ClassVar[str] = "Translate parsed sections to the target language."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "paper_id": {"type": "string"},
            "task_id": {"type": "string"},
            "target_language": {"type": "string", "default": "Chinese"},
        },
        "required": ["paper_id", "task_id"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        from ...harness.pipeline.orchestrator import run_translate_step

        rt = _require_runtime(context)
        result = await run_translate_step(
            paper_id=args["paper_id"],
            task_id=args["task_id"],
            target_language=args.get("target_language") or "Chinese",
            storage=rt.storage,
            broker=rt.broker,
            settings=rt.settings,
        )
        status = ToolResultStatus.SUCCESS if result.get("ok") else ToolResultStatus.ERROR
        return ToolResult(status, json.dumps(result, ensure_ascii=False))


class SummarizePaperTool(Tool):
    name: ClassVar[str] = "summarize_paper"
    description: ClassVar[str] = "Generate structured summary markdown using the selected template."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "paper_id": {"type": "string"},
            "task_id": {"type": "string"},
            "template_name": {"type": "string"},
        },
        "required": ["paper_id", "task_id", "template_name"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        from ...harness.pipeline.orchestrator import run_summarize_step

        rt = _require_runtime(context)
        result = await run_summarize_step(
            paper_id=args["paper_id"],
            task_id=args["task_id"],
            template_name=args["template_name"],
            storage=rt.storage,
            broker=rt.broker,
            settings=rt.settings,
            agent_factory=rt.agent_factory,
        )
        return ToolResult(ToolResultStatus.SUCCESS, json.dumps(result, ensure_ascii=False))


class CritiquePaperTool(Tool):
    name: ClassVar[str] = "critique_paper"
    description: ClassVar[str] = "Run multi-agent ToT critique (generator + evaluator) for innovation suggestions."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "paper_id": {"type": "string"},
            "task_id": {"type": "string"},
            "template_name": {"type": "string"},
        },
        "required": ["paper_id", "task_id", "template_name"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        from ...harness.pipeline.orchestrator import run_critique_step

        rt = _require_runtime(context)
        result = await run_critique_step(
            paper_id=args["paper_id"],
            task_id=args["task_id"],
            template_name=args["template_name"],
            storage=rt.storage,
            broker=rt.broker,
            settings=rt.settings,
            agent_factory=rt.agent_factory,
            collaboration_registry=rt.collaboration_registry,
        )
        return ToolResult(ToolResultStatus.SUCCESS, json.dumps(result, ensure_ascii=False))


parse_paper_tool = ParsePaperTool()
translate_paper_tool = TranslatePaperTool()
summarize_paper_tool = SummarizePaperTool()
critique_paper_tool = CritiquePaperTool()
