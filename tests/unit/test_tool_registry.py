"""ToolRegistry — registration, schema export, execution, dedup."""

from __future__ import annotations

import pytest
from tests.helpers import EchoTool

from app.agent.errors import ToolNotFoundError, ToolRepeatedCallError
from app.agent.registry import ToolRegistry
from app.agent.schemas import AuthContext, ToolResult, ToolResultStatus
from app.tools.base import Tool, ToolContext


@pytest.mark.asyncio
async def test_register_and_execute_echo(auth: AuthContext) -> None:
    reg = ToolRegistry()
    reg.register(EchoTool())
    ctx = ToolContext(session_id="s1", auth=auth)
    result = await reg.execute("echo", {"message": "ping"}, context=ctx)
    assert result.status == ToolResultStatus.SUCCESS
    assert result.content == "ping"


@pytest.mark.asyncio
async def test_unknown_tool_raises(auth: AuthContext) -> None:
    reg = ToolRegistry()
    ctx = ToolContext(session_id="s1", auth=auth)
    with pytest.raises(ToolNotFoundError):
        await reg.execute("missing", {}, context=ctx)


def test_tool_definitions_include_registered_tools() -> None:
    reg = ToolRegistry()
    reg.register(EchoTool())
    defs = reg.get_tool_definitions()
    assert any(d["function"]["name"] == "echo" for d in defs)


@pytest.mark.asyncio
async def test_repeated_identical_calls_blocked(auth: AuthContext) -> None:
    from typing import Any, ClassVar

    class RepeatTool(Tool):
        name: ClassVar[str] = "repeat"
        description: ClassVar[str] = "repeat"
        parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
        deduplicate_repeated_calls: ClassVar[bool] = True

        async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
            return ToolResult(ToolResultStatus.SUCCESS, "ok")

    reg = ToolRegistry()
    reg.register(RepeatTool())
    ctx = ToolContext(session_id="dup-session", auth=auth)
    await reg.execute("repeat", {}, context=ctx)
    await reg.execute("repeat", {}, context=ctx)
    with pytest.raises(ToolRepeatedCallError):
        await reg.execute("repeat", {}, context=ctx)
