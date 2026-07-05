"""Runtime composition root — wire AgentLoop, ToolRegistry, Sandbox, tools."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..sandbox.manager import SandboxManager, SandboxSettings
from .context import ContextEngine
from .llm_factory import default_llm_gateway
from .loop import AgentLoop, RuntimeBundle
from .registry import ToolRegistry
from .state import build_state_store
from .stream import build_stream_bus
from .sub_agent_store import build_sub_agent_store

logger = logging.getLogger(__name__)

_runtime: RuntimeBundle | None = None


def _register_tools(registry: ToolRegistry, sandbox: SandboxManager | None) -> None:
    from ..tools.arxiv_search.tool import tool as arxiv_search
    from ..tools.pipeline_steps.tool import (
        critique_paper_tool,
        parse_paper_tool,
        summarize_paper_tool,
        translate_paper_tool,
    )
    from ..tools.run_skill.tool import tool as run_skill
    from ..tools.spawn_sub_agent.tool import tool as spawn_sub_agent
    from ..tools.wait_sub_agents.tool import tool as wait_sub_agents
    from ..tools.web_search.tool import tool as web_search

    for t in (
        web_search,
        arxiv_search,
        run_skill,
        spawn_sub_agent,
        wait_sub_agents,
        parse_paper_tool,
        translate_paper_tool,
        summarize_paper_tool,
        critique_paper_tool,
    ):
        registry.register(t)

    if sandbox is not None and sandbox.settings.is_configured():
        from ..tools.execute_code.tool import tool as execute_code
        from ..tools.shell_command.tool import tool as shell_command

        registry.register(execute_code)
        registry.register(shell_command)
        registry.register_session_cleanup(sandbox.close_session)


def build_runtime(
    *,
    storage: Any,
    broker: Any,
    skill_registry: Any,
    agent_factory: Any | None = None,
    collaboration_registry: Any | None = None,
    user_settings: Any | None = None,
) -> RuntimeBundle:
    """Construct the agent runtime bundle (idempotent singleton)."""
    global _runtime
    if _runtime is not None:
        _runtime.storage = storage
        _runtime.broker = broker
        _runtime.skill_registry = skill_registry
        _runtime.agent_factory = agent_factory
        _runtime.collaboration_registry = collaboration_registry
        return _runtime

    settings = user_settings or get_settings()
    backend_root = Path(__file__).resolve().parents[2]

    sandbox_settings = SandboxSettings(
        enabled=getattr(settings, "sandbox_enabled", False),
        docker_image=getattr(settings, "sandbox_docker_image", "python:3.12-slim"),
        timeout_sec=getattr(settings, "sandbox_timeout_sec", 120.0),
        workspace_root=str(backend_root / getattr(settings, "sandbox_workspace_root", "data/sandbox")),
        network_enabled=getattr(settings, "sandbox_network_enabled", False),
    )
    sandbox = SandboxManager(sandbox_settings)

    registry = ToolRegistry()
    _register_tools(registry, sandbox)

    stream_bus = build_stream_bus(getattr(settings, "redis_url", None) or None)
    state_store = build_state_store(getattr(settings, "redis_url", None) or None)
    context_engine = ContextEngine()
    sub_agent_store = build_sub_agent_store(
        use_memory=getattr(settings, "sub_agent_store_memory", False),
        auto_fallback=getattr(settings, "sub_agent_store_auto_fallback", True),
    )

    loop = AgentLoop(
        registry=registry,
        stream_bus=stream_bus,
        state_store=state_store,
        context_engine=context_engine,
        sub_agent_store=sub_agent_store,
        llm_factory=default_llm_gateway,
        runtime=None,
    )

    _runtime = RuntimeBundle(
        loop=loop,
        registry=registry,
        stream_bus=stream_bus,
        state_store=state_store,
        context_engine=context_engine,
        sub_agent_store=sub_agent_store,
        sandbox_manager=sandbox,
        skill_registry=skill_registry,
        storage=storage,
        broker=broker,
        agent_factory=agent_factory,
        collaboration_registry=collaboration_registry,
        settings=settings,
    )
    loop._runtime = _runtime
    return _runtime


def get_runtime() -> RuntimeBundle:
    if _runtime is None:
        raise RuntimeError("Agent runtime not initialized — call build_runtime() during AppHarness.startup()")
    return _runtime


def reset_runtime() -> None:
    """Clear singleton runtime (tests only)."""
    global _runtime
    _runtime = None
