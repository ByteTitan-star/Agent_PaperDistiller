"""Governed shell command execution in sandbox."""

from __future__ import annotations

from typing import Any, ClassVar

from ...agent.errors import SandboxNotConfiguredError
from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


class ShellCommandTool(Tool):
    name: ClassVar[str] = "shell_command"
    description: ClassVar[str] = (
        "Run a single shell command inside the session sandbox (ls, grep, pip install, etc.). "
        "Do not use for destructive host operations."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "working_directory": {
                "type": "string",
                "description": "Optional working dir inside sandbox",
            },
        },
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        runtime = context.runtime
        if runtime is None or runtime.sandbox_manager is None:
            raise SandboxNotConfiguredError("Sandbox not configured")
        mgr = runtime.sandbox_manager
        if not mgr.settings.is_configured():
            raise SandboxNotConfiguredError("Docker sandbox unavailable")

        result = await mgr.run_command(
            context.session_id,
            args.get("command") or "",
            working_directory=args.get("working_directory"),
        )
        body = f"exit_code={result.exit_code}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        status = ToolResultStatus.SUCCESS if result.exit_code == 0 else ToolResultStatus.ERROR
        return ToolResult(status, body)


tool = ShellCommandTool()
