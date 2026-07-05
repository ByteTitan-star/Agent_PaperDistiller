# Conftest: ensure backend/ is on sys.path for `import app`

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Unit tests should not require a live MySQL/asyncmy driver at import time.
sys.modules.setdefault("asyncmy", MagicMock())
patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=MagicMock()).start()

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.context import ContextEngine
from app.agent.loop import AgentLoop, RuntimeBundle
from app.agent.models import LLMResponse
from app.agent.registry import ToolRegistry
from app.agent.schemas import AuthContext
from app.agent.state import InMemoryStateStore
from app.agent.stream import InMemoryStreamBus
from app.agent.sub_agent_store import InMemorySubAgentStore
from tests.helpers import EchoTool, MockLLMGateway, MockSkillRegistry


@pytest.fixture
def auth() -> AuthContext:
    return AuthContext(user_id=1, tenant_id="test", paper_id="paper-1")


@pytest.fixture
def tool_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


@pytest.fixture
def runtime_bundle(tool_registry: ToolRegistry, auth: AuthContext) -> RuntimeBundle:
    stream = InMemoryStreamBus()
    state = InMemoryStateStore()
    context = ContextEngine(default_system_prompt="You are a test assistant.")
    sub_store = InMemorySubAgentStore()
    llm = MockLLMGateway([LLMResponse(content="hello from mock")])

    loop = AgentLoop(
        registry=tool_registry,
        stream_bus=stream,
        state_store=state,
        context_engine=context,
        sub_agent_store=sub_store,
        llm_factory=lambda _auth: llm,
        runtime=None,
    )
    bundle = RuntimeBundle(
        loop=loop,
        registry=tool_registry,
        stream_bus=stream,
        state_store=state,
        context_engine=context,
        sub_agent_store=sub_store,
        skill_registry=MockSkillRegistry(
            {
                "web_search": {
                    "results": [{"title": "Paper A", "url": "https://example.com/a", "content": "abstract"}],
                },
            }
        ),
    )
    loop._runtime = bundle
    return bundle
