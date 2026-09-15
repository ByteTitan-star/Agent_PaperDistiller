"""Pipeline orchestrator — mocked step integration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from tests.helpers import MockAgentFactory, MockStorage

from app.harness.pipeline.orchestrator import (
    PaperPipelineOrchestrator,
    PipelineParseError,
    run_parse_step,
    translate_sections_smart,
)
from app.pipeline.document_ir import DocumentIR
from app.pipeline.state_broker import TaskBroker


@pytest.fixture
def pipeline_settings() -> SimpleNamespace:
    return SimpleNamespace(
        max_chunk_chars=900,
        chunk_overlap=120,
        pipeline_translation_retry_limit=1,
        default_collaboration_mode="tot",
        hitl_checkpoints=[],
        translation_provider="google",  # 单测固定走 Google 通道（不依赖 LLM key）
        generation_model_name="DeepSeek-V3",
        evaluation_model_name="Qwen3",
        enable_tot=True,
        tot_branch_count=3,
        tot_generation_temperature=0.8,
        tot_generation_trials=3,
        tot_reviewer_temperature=0.2,
        tot_score_alpha=1.0,
        tot_score_beta=1.0,
        tot_score_gamma=1.0,
    )


def _make_ir(ok: bool = True, sections: list[tuple[str, str]] | None = None) -> DocumentIR:
    from app.pipeline.document_ir import DocNode

    section_title = (sections or [("## 1 引言", "Body text of the introduction.")])[0][0]
    return DocumentIR(
        parser="pymupdf",
        ok=ok,
        sections=sections or [("## 1 引言", "Body text of the introduction.")],
        nodes=[DocNode(node_id="heading-0001", type="heading", text=section_title, page=2, level=2)],
        error="" if ok else "疑似扫描件",
        error_kind="" if ok else "scanned_pdf",
    )


@pytest.mark.asyncio
async def test_run_parse_step_persists_chunks_and_artifact(tmp_path: Path, pipeline_settings: SimpleNamespace) -> None:
    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("task-1", "paper-1")

    with patch("app.harness.pipeline.orchestrator.parse_document", return_value=_make_ir()) as mock_parse:
        result = await run_parse_step(
            paper_id="paper-1",
            task_id="task-1",
            storage=storage,
            broker=broker,
            settings=pipeline_settings,
        )

    assert result["ok"] is True
    assert result["sections"] == 1
    assert result["chunks"] >= 1
    assert storage.chunks["paper-1"]
    assert storage.parse_artifacts["paper-1"] is not None
    # 章节页码（来自标题节点）贯通到块元数据
    metas = storage.chunk_metas["paper-1"] or []
    assert metas and metas[0]["page"] == 2
    assert metas[0]["section"] == "1 引言"
    mock_parse.assert_called_once()
    state = await broker.get("task-1")
    assert state is not None
    assert state.status == "parsing"


@pytest.mark.asyncio
async def test_run_parse_step_failure_propagates(tmp_path: Path, pipeline_settings: SimpleNamespace) -> None:
    """解析失败（扫描件）必须返回 ok=False，而不是把错误文案当正文。"""
    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("task-f", "paper-f")

    with patch(
        "app.harness.pipeline.orchestrator.parse_document",
        return_value=_make_ir(ok=False),
    ):
        result = await run_parse_step(
            paper_id="paper-f",
            task_id="task-f",
            storage=storage,
            broker=broker,
            settings=pipeline_settings,
        )

    assert result["ok"] is False
    assert result["error_kind"] == "scanned_pdf"
    assert storage.chunks == {}  # 失败时不产块


@pytest.mark.asyncio
async def test_parse_once_across_steps(tmp_path: Path, pipeline_settings: SimpleNamespace) -> None:
    """translate 复用 parse 产物与翻译产物：整个管线只解析一次、只翻译一次。"""
    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("task-3", "paper-3")

    with (
        patch("app.harness.pipeline.orchestrator.parse_document", return_value=_make_ir()) as mock_parse,
        patch(
            "app.harness.pipeline.orchestrator.translate_sections",
            return_value=([("## 1 引言", "引言正文。")], 0),
        ) as mock_translate,
        patch(
            "app.harness.pipeline.orchestrator.resolve_template_content",
            new=AsyncMock(return_value="# Template"),
        ),
        patch("app.harness.pipeline.orchestrator.make_summary_markdown", new=AsyncMock(return_value="# summary")),
        patch(
            "app.harness.pipeline.orchestrator.make_improvement_markdown",
            new=AsyncMock(return_value="# improvement"),
        ),
    ):
        runtime = SimpleNamespace(
            storage=storage,
            broker=broker,
            agent_factory=MockAgentFactory(),
            collaboration_registry=None,
            settings=pipeline_settings,
        )
        orchestrator = PaperPipelineOrchestrator(runtime)
        await orchestrator.run(
            task_id="task-3",
            paper_id="paper-3",
            title="Parse Once",
            target_language="Chinese",
            template_name="tinghua.md",
            settings=pipeline_settings,
            user_id=None,
        )

    mock_parse.assert_called_once()  # 四个步骤共享一次解析
    mock_translate.assert_called_once()  # translate/summarize/critique 共享一次翻译
    assert storage.parse_artifacts["paper-3"] is not None


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
async def test_pipeline_raises_on_parse_failure(tmp_path: Path, pipeline_settings: SimpleNamespace) -> None:
    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("task-e", "paper-e")
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
            new=AsyncMock(return_value={"ok": False, "error_kind": "scanned_pdf", "error": "no text layer"}),
        ),
        pytest.raises(PipelineParseError, match="扫描件"),
    ):
        await orchestrator.run(
            task_id="task-e",
            paper_id="paper-e",
            title="Bad PDF",
            target_language="Chinese",
            template_name="tinghua.md",
            settings=pipeline_settings,
            user_id=None,
        )


@pytest.mark.asyncio
async def test_translate_sections_smart_falls_back_to_google(pipeline_settings: SimpleNamespace) -> None:
    """auto 模式下无 API key -> 直接走 Google 通道。"""
    sections = [("## 1 引言", "Some english body.")]
    with patch(
        "app.harness.pipeline.orchestrator.translate_sections",
        return_value=([(sections[0][0], "中文正文。")], 0),
    ) as mock_google:
        translated, failures, provider = await translate_sections_smart(sections, "Chinese", pipeline_settings)

    assert provider == "google"
    assert failures == 0
    assert translated[0][1] == "中文正文。"
    mock_google.assert_called_once()


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
