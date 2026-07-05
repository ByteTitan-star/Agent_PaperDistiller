"""AgentLoop integration — mock LLM drives tool call then final answer."""

from __future__ import annotations

import pytest
from tests.helpers import MockLLMGateway

from app.agent.context import ContextEngine, WorkingSet
from app.agent.loop import AgentLoop, TurnConfig
from app.agent.models import LLMResponse
from app.agent.schemas import ToolCall
from app.agent.state import InMemoryStateStore
from app.agent.stream import InMemoryStreamBus
from app.agent.sub_agent_store import InMemorySubAgentStore


@pytest.mark.asyncio
async def test_drive_tool_then_answer(auth, tool_registry) -> None:
    llm = MockLLMGateway(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCall(id="tc1", name="echo", arguments={"message": "tool-ok"})],
            ),
            LLMResponse(content="final answer"),
        ]
    )
    loop = AgentLoop(
        registry=tool_registry,
        stream_bus=InMemoryStreamBus(),
        state_store=InMemoryStateStore(),
        context_engine=ContextEngine(),
        sub_agent_store=InMemorySubAgentStore(),
        llm_factory=lambda _a: llm,
    )
    ws = WorkingSet(system_prompt="test")
    ws.append_user("hello")

    answer, chain, _prompt_tok, _completion_tok = await loop._drive(
        session_id="sess-1",
        auth=auth,
        working_set=ws,
        cfg=TurnConfig(max_iterations=5),
        run_id="run-1",
        thinking_chain=[],
    )

    assert answer == "final answer"
    assert any(item.get("type") == "tool_call" for item in chain)
    assert any(item.get("type") == "tool_result" for item in chain)
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_spawn_sub_agent_inline(runtime_bundle, auth) -> None:
    from app.agent.models import LLMResponse

    runtime_bundle.loop._llm_factory = lambda _a: MockLLMGateway([LLMResponse(content="sub-result")])

    handle = await runtime_bundle.loop.spawn_sub_agent(
        parent_session_id="parent-1",
        auth=auth,
        task="summarize section 2",
        wait="inline",
    )
    assert handle.status == "done"
    assert handle.result == "sub-result"

    stored = await runtime_bundle.sub_agent_store.get(handle.handle_id)
    assert stored is not None
    assert stored.status == "done"
