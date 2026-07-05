"""Tool registry — discovery, schema coercion, throttling, execution."""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools.base import INTENT_SUMMARY_KEY, Tool, ToolContext
from .errors import (
    ToolArgumentError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRepeatedCallError,
)
from .schemas import ToolResult, ToolResultStatus

logger = logging.getLogger(__name__)

_REPEATED_CALL_THRESHOLD = 2
_EMPTY_OUTPUT_TEMPLATE = "({name} completed with no output)"
_ERROR_STRATEGY_HINT = (
    "[Tool FAILED — do NOT repeat with identical arguments. Fix args, try another tool, "
    "or explain the limitation to the user.]"
)
_CONTENT_HARD_LIMIT = 100 * 1024
_TRUNCATION_SUFFIX = "\n\n[... truncated ...]"

SessionCleanupHook = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    concurrency_safe: bool


class ToolRegistry:
    """Single execution surface for all agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._call_counts: dict[str, dict[str, int]] = {}
        self._session_cleanup_hooks: list[SessionCleanupHook] = []

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool.name is required")
        self._tools[tool.name] = tool

    def discover(self, tools_dir: Path) -> int:
        count = 0
        if not tools_dir.is_dir():
            return 0
        for child in sorted(tools_dir.iterdir()):
            module_path = child / "tool.py"
            if not module_path.is_file():
                continue
            module = importlib.import_module(f"app.tools.{child.name}.tool")
            seen: set[str] = set()
            candidates: list[Tool] = []
            primary = getattr(module, "tool", None)
            if isinstance(primary, Tool):
                candidates.append(primary)
            for attr_name in dir(module):
                if not attr_name.endswith("_tool"):
                    continue
                obj = getattr(module, attr_name)
                if isinstance(obj, Tool):
                    candidates.append(obj)
            for tool in candidates:
                if tool.name in seen:
                    continue
                seen.add(tool.name)
                self.register(tool)
                count += 1
        return count

    def list_tools(self) -> list[ToolInfo]:
        return [
            ToolInfo(t.name, t.description, t.concurrency_safe)
            for t in sorted(self._tools.values(), key=lambda x: x.name)
        ]

    def get_tool_definitions(self, mask: set[str] | None = None) -> list[dict[str, Any]]:
        defs: list[dict[str, Any]] = []
        for name in sorted(self._tools.keys()):
            if mask is not None and name not in mask:
                continue
            tool = self._tools[name]
            defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return defs

    def clear_session(self, session_id: str) -> None:
        self._call_counts.pop(session_id, None)

    def register_session_cleanup(self, hook: SessionCleanupHook) -> None:
        self._session_cleanup_hooks.append(hook)

    async def close_session(self, session_id: str) -> None:
        self.clear_session(session_id)
        for hook in self._session_cleanup_hooks:
            try:
                await hook(session_id)
            except Exception:
                logger.exception("Session cleanup hook failed session=%s", session_id)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: ToolContext,
    ) -> ToolResult:
        if name not in self._tools:
            raise ToolNotFoundError(f"Unknown tool: {name}")

        tool = self._tools[name]
        args = dict(arguments or {})
        args.pop(INTENT_SUMMARY_KEY, None)

        signature = json.dumps({"tool": name, "args": args}, sort_keys=True, ensure_ascii=False)
        session_counts = self._call_counts.setdefault(context.session_id, {})
        count = session_counts.get(signature, 0) + 1
        session_counts[signature] = count
        if tool.deduplicate_repeated_calls and count > _REPEATED_CALL_THRESHOLD:
            raise ToolRepeatedCallError(f"Repeated identical call blocked for {name}")

        try:
            self._validate_args(tool.parameters, args)
        except Exception as exc:
            raise ToolArgumentError(str(exc)) from exc

        try:
            result = await tool.execute(args, context)
        except ToolRepeatedCallError:
            raise
        except Exception as exc:
            logger.exception("Tool execution failed tool=%s", name)
            raise ToolExecutionError(str(exc)) from exc

        return self._post_process(name, result)

    def _validate_args(self, schema: dict[str, Any], args: dict[str, Any]) -> None:
        required = schema.get("required") or []
        props = schema.get("properties") or {}
        for key in required:
            if key not in args:
                raise ToolArgumentError(f"Missing required argument: {key}")
        for key in args:
            if key not in props and schema.get("additionalProperties") is not True:
                # lax mode: allow unknown keys
                continue

    def _post_process(self, name: str, result: ToolResult) -> ToolResult:
        content = result.content or _EMPTY_OUTPUT_TEMPLATE.format(name=name)
        if len(content) > _CONTENT_HARD_LIMIT:
            content = content[: _CONTENT_HARD_LIMIT - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
        if result.status == ToolResultStatus.ERROR:
            content = f"{content}\n\n{_ERROR_STRATEGY_HINT}"
        return ToolResult(
            status=result.status,
            content=content,
            metadata=result.metadata,
            artifacts=result.artifacts,
        )
