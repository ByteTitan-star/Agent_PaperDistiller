"""Agent tools — wiring to runtime dependencies."""

from __future__ import annotations

import json

import pytest
from tests.helpers import MockLLMGateway

from app.agent.schemas import ToolResultStatus
from app.tools.base import ToolContext
from app.tools.run_skill.tool import tool as run_skill
from app.tools.spawn_sub_agent.tool import tool as spawn_sub_agent
from app.tools.wait_sub_agents.tool import tool as wait_sub_agents
from app.tools.web_search.tool import tool as web_search


@pytest.mark.asyncio
async def test_web_search_via_skill_registry(runtime_bundle, auth) -> None:
    ctx = ToolContext(session_id="s1", auth=auth, runtime=runtime_bundle)
    result = await web_search.execute({"query": "transformer", "max_results": 2}, ctx)
    assert result.status == ToolResultStatus.SUCCESS
    assert "Paper A" in result.content
    assert runtime_bundle.skill_registry.calls[0][0] == "web_search"


@pytest.mark.asyncio
async def test_web_search_without_runtime(auth) -> None:
    ctx = ToolContext(session_id="s1", auth=auth, runtime=None)
    result = await web_search.execute({"query": "x"}, ctx)
    assert result.status == ToolResultStatus.ERROR


@pytest.mark.asyncio
async def test_run_skill_success(runtime_bundle, auth) -> None:
    runtime_bundle.skill_registry.responses["arxiv_search"] = {"papers": [{"title": "Attention"}]}
    ctx = ToolContext(session_id="s1", auth=auth, runtime=runtime_bundle)
    result = await run_skill.execute({"skill_name": "arxiv_search", "arguments": {"query": "attention"}}, ctx)
    assert result.status == ToolResultStatus.SUCCESS
    payload = json.loads(result.content)
    assert "papers" in payload


@pytest.mark.asyncio
async def test_spawn_sub_agent_tool(runtime_bundle, auth) -> None:
    from app.agent.models import LLMResponse

    runtime_bundle.loop._llm_factory = lambda _a: MockLLMGateway([LLMResponse(content="delegated")])

    ctx = ToolContext(session_id="parent", auth=auth, runtime=runtime_bundle)
    result = await spawn_sub_agent.execute({"task": "find related work", "wait": "inline"}, ctx)
    assert result.status == ToolResultStatus.SUCCESS
    payload = json.loads(result.content)
    assert payload["status"] == "done"
    assert payload["result"] == "delegated"


@pytest.mark.asyncio
async def test_wait_sub_agents_collects_done_handles(runtime_bundle, auth) -> None:
    import asyncio

    from app.agent.models import LLMResponse

    runtime_bundle.loop._llm_factory = lambda _a: MockLLMGateway([LLMResponse(content="async-done")])

    spawn_ctx = ToolContext(session_id="parent2", auth=auth, runtime=runtime_bundle)
    spawn_result = await spawn_sub_agent.execute({"task": "async task", "wait": "fanin"}, spawn_ctx)
    handle_id = json.loads(spawn_result.content)["handle_id"]

    await asyncio.sleep(0.2)

    wait_ctx = ToolContext(session_id="parent2", auth=auth, runtime=runtime_bundle)
    wait_result = await wait_sub_agents.execute({"handle_ids": [handle_id], "timeout_sec": 5}, wait_ctx)
    assert wait_result.status == ToolResultStatus.SUCCESS
    rows = json.loads(wait_result.content)
    assert rows[0]["status"] == "done"
