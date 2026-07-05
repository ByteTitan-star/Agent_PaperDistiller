"""Pipeline orchestrator — mocked step integration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tests.helpers import MockAgentFactory, MockStorage

from app.harness.pipeline.orchestrator import PaperPipelineOrchestrator, run_parse_step
from app.pipeline.state_broker import TaskBroker


@pytest.fixture
def pipeline_settings() -> SimpleNamespace:
    return SimpleNamespace(
        max_chunk_chars=900,
        chunk_overlap=120,
        pipeline_translation_retry_limit=1,
        default_collaboration_mode="tot",
        hitl_checkpoints=[],
    )


@pytest.mark.asyncio
async def test_run_parse_step_persists_chunks(tmp_path: Path, pipeline_settings: SimpleNamespace) -> None:
    storage = MockStorage(tmp_path, pdf_text="INTRODUCTION\n\nThis is a test paper.")
    broker = TaskBroker()
    await broker.create("task-1", "paper-1")

    with (
        patch("app.harness.pipeline.orchestrator.extract_text_from_pdf", return_value="INTRODUCTION\n\nBody."),
        patch("app.harness.pipeline.orchestrator.split_text_into_sections", return_value=[("INTRODUCTION", "Body.")]),
        patch("app.harness.pipeline.orchestrator.chunk_text", return_value=["chunk-a", "chunk-b"]),
    ):
        result = await run_parse_step(
            paper_id="paper-1",
            task_id="task-1",
            storage=storage,
            broker=broker,
            settings=pipeline_settings,
        )

    assert result["ok"] is True
    assert result["chunks"] == 2
    assert storage.chunks["paper-1"] == ["chunk-a", "chunk-b"]
    state = await broker.get("task-1")
    assert state is not None
    assert state.status == "parsing"


@pytest.mark.asyncio
async def test_pipeline_orchestrator_full_run(tmp_path: Path, pipeline_settings: SimpleNamespace) -> None:
    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("task-2", "paper-2")

    runtime = SimpleNamespace(
        storage=storage,
        broker=broker,
        agent_factory=MockAgentFactory(),
        collaboration_registry=None,
        settings=pipeline_settings,
    )
    orchestrator = PaperPipelineOrchestrator(runtime)

    with (
        patch(
            "app.harness.pipeline.orchestrator.run_parse_step",
            new=AsyncMock(return_value={"ok": True, "chunks": 1}),
        ),
        patch(
            "app.harness.pipeline.orchestrator.run_translate_step",
            new=AsyncMock(return_value={"ok": True, "translation_failures": 0}),
        ),
        patch(
            "app.harness.pipeline.orchestrator.run_summarize_step",
            new=AsyncMock(return_value={"ok": True, "tags": ["General"]}),
        ),
        patch(
            "app.harness.pipeline.orchestrator.run_critique_step",
            new=AsyncMock(return_value={"ok": True, "collaboration_mode": "ToT"}),
        ),
    ):
        tags = await orchestrator.run(
            task_id="task-2",
            paper_id="paper-2",
            title="Test Paper",
            target_language="Chinese",
            template_name="tinghua.md",
            settings=pipeline_settings,
            user_id=1,
        )

    assert tags == ["General"]


@pytest.mark.asyncio
async def test_parse_paper_tool_calls_orchestrator(runtime_bundle, auth) -> None:
    from app.tools.base import ToolContext
    from app.tools.pipeline_steps.tool import parse_paper_tool

    called = {"ok": False}

    async def fake_parse(**kwargs: object) -> dict[str, object]:
        called["ok"] = True
        assert kwargs["paper_id"] == "p-99"
        return {"ok": True, "chunks": 3}

    runtime_bundle.storage = MockStorage(Path("/tmp/unused"))
    runtime_bundle.broker = TaskBroker()

    with patch("app.harness.pipeline.orchestrator.run_parse_step", new=fake_parse):
        ctx = ToolContext(session_id="s-pipe", auth=auth, runtime=runtime_bundle)
        result = await parse_paper_tool.execute({"paper_id": "p-99", "task_id": "t-99"}, ctx)

    assert called["ok"] is True
    assert "ok" in result.content
