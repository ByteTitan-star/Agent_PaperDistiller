"""P0 regression tests — per-request settings and resilient stores."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from tests.helpers import MockLLMGateway

from app.agent.context import ContextEngine, WorkingSet
from app.agent.llm_factory import make_llm_gateway
from app.agent.loop import AgentLoop, TurnConfig
from app.agent.schemas import AuthContext
from app.agent.state import InMemoryStateStore
from app.agent.stream import InMemoryStreamBus
from app.agent.sub_agent_store import InMemorySubAgentStore, ResilientSubAgentStore


def test_make_llm_gateway_uses_settings_snapshot() -> None:
    settings = SimpleNamespace(
        deepseek_api_key="test-secret",
        deepseek_base_url="https://example.com",
        deepseek_model="test-model",
        deepseek_timeout_sec=12.0,
    )
    gateway = make_llm_gateway(settings, auth=AuthContext(user_id=1))
    assert gateway.model == "test-model"
    assert gateway._client.api_key == "test-secret"


@pytest.mark.asyncio
async def test_agent_loop_uses_turn_user_settings(auth, tool_registry) -> None:
    from app.agent.models import LLMResponse

    user_settings = SimpleNamespace(
        deepseek_api_key="sk-test",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
        deepseek_timeout_sec=30.0,
    )
    llm = MockLLMGateway([LLMResponse(content="ok")])
    loop = AgentLoop(
        registry=tool_registry,
        stream_bus=InMemoryStreamBus(),
        state_store=InMemoryStateStore(),
        context_engine=ContextEngine(),
        sub_agent_store=InMemorySubAgentStore(),
        llm_factory=lambda _a: llm,
    )
    ws = WorkingSet(system_prompt="sys")
    ws.append_user("hi")

    with patch("app.agent.llm_factory.make_llm_gateway", return_value=llm) as make_gateway:
        await loop._drive(
            session_id="s-user",
            auth=auth,
            working_set=ws,
            cfg=TurnConfig(user_settings=user_settings, max_iterations=1),
            run_id="run-user",
            thinking_chain=[],
        )
        make_gateway.assert_called_once()
        assert make_gateway.call_args.args[0] is user_settings

    assert llm.calls


@pytest.mark.asyncio
async def test_loop_emits_history_warning_when_unavailable(auth, tool_registry) -> None:
    from app.agent.schemas import EventType

    loop = AgentLoop(
        registry=tool_registry,
        stream_bus=InMemoryStreamBus(),
        state_store=InMemoryStateStore(),
        context_engine=ContextEngine(),
        sub_agent_store=InMemorySubAgentStore(),
        llm_factory=lambda _a: MockLLMGateway([]),
    )
    ws = WorkingSet(system_prompt="sys", history_loaded=False, history_error="db down")
    emitted: list[EventType] = []

    async def capture_emit(
        session_id: str,
        event_type: EventType,
        data: dict,
        *,
        run_id: str | None = None,
    ) -> None:
        emitted.append(event_type)

    loop._emit = capture_emit  # type: ignore[method-assign]
    await loop._maybe_emit_history_warning("sess-hist", ws, run_id="run-1")
    assert EventType.STAGE in emitted


@pytest.mark.asyncio
async def test_resilient_sub_agent_store_falls_back_to_memory() -> None:
    store = ResilientSubAgentStore()
    with patch.object(store._sql, "create", side_effect=RuntimeError("db unavailable")):
        handle = await store.create(
            parent_session_id="p",
            child_session_id="p:sub:1",
            user_id=1,
            task_summary="task",
        )
    assert handle.status == "pending"
    assert store._use_memory is True
    fetched = await store.get(handle.handle_id)
    assert fetched is not None
