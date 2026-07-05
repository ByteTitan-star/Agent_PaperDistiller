"""Verify critical modules import without side-effect failures."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
TOOLS_DIR = BACKEND / "app" / "tools"

CORE_MODULES = [
    "app.agent.bootstrap",
    "app.agent.loop",
    "app.agent.registry",
    "app.agent.schemas",
    "app.agent.context",
    "app.agent.state",
    "app.agent.stream",
    "app.agent.sub_agent_store",
    "app.agent.models",
    "app.agent.hitl",
    "app.services.hitl_coordinator",
    "app.services.hitl_sse",
    "app.services.hitl_persistence",
    "app.services.deep_search_hitl",
    "app.tools.base",
    "app.tools.web_search.tool",
    "app.tools.arxiv_search.tool",
    "app.tools.run_skill.tool",
    "app.tools.spawn_sub_agent.tool",
    "app.tools.wait_sub_agents.tool",
    "app.tools.pipeline_steps.tool",
    "app.tools.execute_code.tool",
    "app.tools.shell_command.tool",
    "app.sandbox.manager",
]


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_core_module_imports(module_name: str) -> None:
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_tool_discover_finds_packaged_tools() -> None:
    from app.agent.registry import ToolRegistry

    registry = ToolRegistry()
    count = registry.discover(TOOLS_DIR)
    names = {t.name for t in registry.list_tools()}
    # One `tool` export per package; pipeline_steps also exposes *_tool aliases.
    assert count >= 10
    assert "web_search" in names
    assert "parse_paper" in names
    assert "spawn_sub_agent" in names
