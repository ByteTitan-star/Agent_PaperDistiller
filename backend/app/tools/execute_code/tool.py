"""Execute Python/bash/R code inside the session sandbox."""

from __future__ import annotations

from typing import Any, ClassVar

from ...agent.errors import SandboxNotConfiguredError
from ...agent.schemas import ToolResult, ToolResultStatus
from ...tools.base import Tool, ToolContext


class ExecuteCodeTool(Tool):
    name: ClassVar[str] = "execute_code"
    description: ClassVar[str] = (
        "Run Python, bash, or R code in an isolated sandbox workspace. "
        "Use for data analysis, plotting, file transforms — never for host commands."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "language": {"type": "string", "enum": ["python", "bash", "r"], "default": "python"},
            "code": {"type": "string", "description": "Source code to execute"},
            "filename": {
                "type": "string",
                "description": "Optional script filename",
                "default": "main.py",
            },
        },
        "required": ["code"],
    }

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        runtime = context.runtime
        if runtime is None or runtime.sandbox_manager is None:
            raise SandboxNotConfiguredError("Sandbox not configured")
        mgr = runtime.sandbox_manager
        if not mgr.settings.is_configured():
            raise SandboxNotConfiguredError("Docker sandbox unavailable")

        language = (args.get("language") or "python").lower()
        code = args.get("code") or ""
        filename = args.get("filename") or {
            "python": "main.py",
            "bash": "run.sh",
            "r": "main.R",
        }.get(language, "main.py")

        await mgr.write_file(context.session_id, filename, code)
        if language == "python":
            result = await mgr.run_python_file(context.session_id, filename)
        elif language == "bash":
            result = await mgr.run_command(context.session_id, f"bash {filename}")
        else:
            result = await mgr.run_command(context.session_id, f"Rscript {filename}")

        body = f"exit_code={result.exit_code}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        status = ToolResultStatus.SUCCESS if result.exit_code == 0 else ToolResultStatus.ERROR
        return ToolResult(status, body)


tool = ExecuteCodeTool()
