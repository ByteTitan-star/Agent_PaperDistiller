"""document_jobs 状态机（fake session）+ GROBID references 合并去重（fixture）测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.pipeline.document_ir import DocNode, DocumentIR
from app.pipeline.grobid import _ref_fingerprint, enrich_ir_metadata, merge_reference_nodes
from app.services.document_jobs import (
    FAILED,
    create_document_job,
    transition_document_job,
    validate_transition,
)

# ---------------------------------------------------------------------
# 状态机：流转校验（纯函数）
# ---------------------------------------------------------------------


def test_validate_transition_linear_forward() -> None:
    assert validate_transition(None, "UPLOADED") is True
    assert validate_transition("UPLOADED", "PARSING") is True
    assert validate_transition("PARSING", "CHUNKING") is True
    assert validate_transition("CHUNKING", "EMBEDDING") is True
    assert validate_transition("EMBEDDING", "INDEXED") is True


def test_validate_transition_rules() -> None:
    assert validate_transition("PARSING", "PARSING") is True  # 幂等
    assert validate_transition("CHUNKING", "PARSING") is False  # 禁止回退
    assert validate_transition("PARSING", "EMBEDDING") is False  # 禁止跳阶段
    assert validate_transition("PARSING", FAILED) is True  # 任意阶段可失败
    assert validate_transition(FAILED, "INDEXED") is False  # FAILED 终态
    assert validate_transition(FAILED, FAILED) is False
    assert validate_transition("PARSING", "UPLOADING") is False  # 非法阶段名
    assert validate_transition(None, "PARSING") is False  # 初始只能 UPLOADED


# ---------------------------------------------------------------------
# 状态机：fake session 持久化
# ---------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row: object) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object:
        return self._row


class _FakeSession:
    def __init__(self) -> None:
        self.job: object = None
        self.commits = 0

    async def execute(self, _query: object) -> _FakeResult:
        return _FakeResult(self.job)

    def add(self, obj: object) -> None:
        self.job = obj

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        self.commits += 1


class _FakeCtx:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


def _factory(session: _FakeSession):
    return lambda: _FakeCtx(session)


@pytest.mark.asyncio
async def test_create_and_full_lifecycle() -> None:
    session = _FakeSession()

    created = await create_document_job("p1", "t1", session_factory=_factory(session))
    assert created is True
    assert session.job.stage == "UPLOADED"
    assert session.job.stage_history[0]["stage"] == "UPLOADED"

    for stage in ("PARSING", "CHUNKING", "EMBEDDING", "INDEXED"):
        ok = await transition_document_job("p1", "t1", stage, parser="pymupdf", session_factory=_factory(session))
        assert ok is True
        assert session.job.stage == stage

    history_stages = [entry["stage"] for entry in session.job.stage_history]
    assert history_stages == ["UPLOADED", "PARSING", "CHUNKING", "EMBEDDING", "INDEXED"]
    assert session.job.parser == "pymupdf"
    assert session.commits == 5


@pytest.mark.asyncio
async def test_transition_backward_rejected() -> None:
    session = _FakeSession()
    await create_document_job("p2", "t2", session_factory=_factory(session))
    await transition_document_job("p2", "t2", "PARSING", session_factory=_factory(session))
    await transition_document_job("p2", "t2", "CHUNKING", session_factory=_factory(session))
    # 跳阶段与回退均被拒绝
    assert await transition_document_job("p2", "t2", "INDEXED", session_factory=_factory(session)) is False
    assert await transition_document_job("p2", "t2", "PARSING", session_factory=_factory(session)) is False
    assert session.job.stage == "CHUNKING"


@pytest.mark.asyncio
async def test_failed_carries_error() -> None:
    session = _FakeSession()
    await create_document_job("p3", "t3", session_factory=_factory(session))
    ok = await transition_document_job(
        "p3", "t3", FAILED, parser="router", error="疑似扫描件", session_factory=_factory(session)
    )
    assert ok is True
    assert session.job.stage == FAILED
    assert session.job.error == "疑似扫描件"
    assert session.job.stage_history[-1]["error"] == "疑似扫描件"


@pytest.mark.asyncio
async def test_db_exception_is_swallowed() -> None:
    def broken_factory():
        raise RuntimeError("db down")

    assert await create_document_job("p4", "t4", session_factory=broken_factory) is False
    assert await transition_document_job("p4", "t4", "PARSING", session_factory=broken_factory) is False


# ---------------------------------------------------------------------
# orchestrator 接线：阶段顺序断言
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_step_records_job_stages(tmp_path):
    from tests.helpers import MockStorage

    from app.harness.pipeline.orchestrator import run_parse_step
    from app.pipeline.state_broker import TaskBroker

    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("t-job", "p-job")

    stages: list[str] = []
    ir = DocumentIR(
        parser="pymupdf",
        sections=[("## 1 引言", "正文。")],
        nodes=[DocNode(node_id="h1", type="heading", text="## 1 引言", page=1, level=2)],
    )

    async def fake_transition(paper_id, task_id, stage, **kwargs):
        stages.append(stage)
        return True

    with (
        patch("app.services.document_jobs.create_document_job", new=AsyncMock(return_value=True)),
        patch("app.services.document_jobs.transition_document_job", side_effect=fake_transition),
        patch("app.harness.pipeline.orchestrator.parse_document", return_value=ir),
    ):
        result = await run_parse_step(
            paper_id="p-job",
            task_id="t-job",
            storage=storage,
            broker=broker,
            settings=SimpleNamespace(max_chunk_chars=300, chunk_overlap=30),
        )

    assert result["ok"] is True
    assert stages == ["PARSING", "CHUNKING", "EMBEDDING", "INDEXED"]


@pytest.mark.asyncio
async def test_parse_step_failure_records_failed(tmp_path):
    from tests.helpers import MockStorage

    from app.harness.pipeline.orchestrator import run_parse_step
    from app.pipeline.state_broker import TaskBroker

    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("t-bad", "p-bad")

    calls: list[dict] = []

    async def fake_transition(paper_id, task_id, stage, **kwargs):
        calls.append({"stage": stage, **kwargs})
        return True

    failed = DocumentIR(ok=False, error_kind="scanned_pdf", error="no text layer")

    with (
        patch("app.services.document_jobs.create_document_job", new=AsyncMock(return_value=True)),
        patch("app.services.document_jobs.transition_document_job", side_effect=fake_transition),
        patch("app.harness.pipeline.orchestrator.parse_document", return_value=failed),
    ):
        result = await run_parse_step(
            paper_id="p-bad",
            task_id="t-bad",
            storage=storage,
            broker=broker,
            settings=SimpleNamespace(max_chunk_chars=300, chunk_overlap=30),
        )

    assert result["ok"] is False
    assert calls[-1]["stage"] == FAILED
    assert calls[-1]["error"] == "no text layer"


# ---------------------------------------------------------------------
# GROBID references 合并去重（fixture）
# ---------------------------------------------------------------------


TEI_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>Attention Is All You Need</title>
        <author><persName><forename>Ashish</forename><surname>Vaswani</surname></persName></author>
      </titleStmt>
    </fileDesc>
  </teiHeader>
  <text><body>
    <listBibl>
      <biblStruct><title>Attention Is All You Need</title></biblStruct>
      <biblStruct><title>Sequence to Sequence Learning with Neural Networks</title></biblStruct>
    </listBibl>
  </body></text>
</TEI>
"""


def test_ref_fingerprint_normalization() -> None:
    assert _ref_fingerprint("Attention Is All You Need!") == _ref_fingerprint("attention is all you need")
    assert _ref_fingerprint("[12] Foo-Bar, 2020.") == _ref_fingerprint("foobar 2020")
    assert len(_ref_fingerprint("x" * 200)) == 60  # 截断保护


def test_merge_matches_and_appends() -> None:
    ir = DocumentIR(
        nodes=[
            DocNode(
                node_id="ref-0001", type="reference", text="[1] Vaswani et al. Attention Is All You Need. NeurIPS 2017."
            ),
            DocNode(node_id="ref-0002", type="reference", text="[2] Some unrelated prior work on optimization."),
        ]
    )
    merged, added = merge_reference_nodes(
        ir,
        [
            "Attention Is All You Need",  # 命中 ref-0001（截断包含）
            "Sequence to Sequence Learning with Neural Networks",  # 未命中 -> 追加
        ],
    )
    assert merged == 1
    assert added == 1
    assert ir.nodes[0].meta["source"] == "grobid"
    assert ir.nodes[0].meta["grobid_title"] == "Attention Is All You Need"
    new_refs = [n for n in ir.nodes if n.node_id.startswith("ref-g")]
    assert len(new_refs) == 1
    assert new_refs[0].meta == {"source": "grobid"}


def test_merge_is_idempotent_no_duplicates() -> None:
    ir = DocumentIR(nodes=[])
    refs = ["Attention Is All You Need", "Sequence to Sequence Learning with Neural Networks"]
    first_merged, first_added = merge_reference_nodes(ir, refs)
    second_merged, second_added = merge_reference_nodes(ir, refs)  # 重放：全部命中，零新增

    assert (first_merged, first_added) == (0, 2)
    assert (second_merged, second_added) == (2, 0)
    assert len([n for n in ir.nodes if n.type == "reference"]) == 2  # 无重复节点


def test_merge_skips_short_entries() -> None:
    ir = DocumentIR(nodes=[])
    merged, added = merge_reference_nodes(ir, ["ab", "short", "A Very Long Reference Title Here"])
    assert (merged, added) == (0, 1)  # 短指纹（<8）跳过，防误配


def test_enrich_ir_metadata_merges_references(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-fake")
    settings = SimpleNamespace(grobid_enabled=True, grobid_base_url="http://grobid", grobid_timeout_sec=5.0)
    ir = DocumentIR(
        sections=[("## 参考文献", "[1] Vaswani et al. Attention Is All You Need. 2017.")],
        nodes=[
            DocNode(node_id="ref-0001", type="reference", text="[1] Vaswani et al. Attention Is All You Need. 2017.")
        ],
    )

    from app.pipeline.grobid import GrobidClient

    with (
        patch.object(GrobidClient, "available", return_value=True),
        patch.object(GrobidClient, "extract_tei", return_value=TEI_FIXTURE),
    ):
        enriched, note = enrich_ir_metadata(ir, pdf, settings)

    assert enriched is True and note == "ok"
    refs = [n for n in ir.nodes if n.type == "reference"]
    assert len(refs) == 2  # 1 个原节点命中 + 1 个 GROBID 新增，无重复
    assert refs[0].meta.get("source") == "grobid"
    assert refs[1].meta.get("source") == "grobid"
