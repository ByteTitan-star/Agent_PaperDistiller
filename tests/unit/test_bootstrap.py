"""Runtime bootstrap wiring."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest

from app.agent.bootstrap import build_runtime, reset_runtime


@pytest.fixture(autouse=True)
def _reset_runtime_singleton() -> Generator[None]:
    reset_runtime()
    yield
    reset_runtime()


def test_build_runtime_registers_core_tools() -> None:
    settings = MagicMock()
    settings.sandbox_enabled = False
    settings.sandbox_docker_image = "python:3.12-slim"
    settings.sandbox_timeout_sec = 60.0
    settings.sandbox_workspace_root = "data/sandbox"
    settings.sandbox_network_enabled = False
    settings.redis_url = ""
    settings.sub_agent_store_memory = True
    settings.deepseek_api_key = "test"
    settings.deepseek_base_url = "https://example.com"
    settings.deepseek_model = "test-model"
    settings.deepseek_timeout_sec = 30.0

    bundle = build_runtime(
        storage=MagicMock(),
        broker=MagicMock(),
        skill_registry=MagicMock(),
        user_settings=settings,
    )
    names = {t.name for t in bundle.registry.list_tools()}
    assert "web_search" in names
    assert "parse_paper" in names
    assert "spawn_sub_agent" in names
    assert bundle.loop._runtime is bundle
