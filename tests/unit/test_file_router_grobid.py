"""FileRouter（Markdown/DOCX）+ GROBID 元数据增强 + 确定性 chunk ID 测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.pipeline.document_ir import DocumentIR
from app.pipeline.document_parser import parse_document
from app.pipeline.grobid import (
    GrobidClient,
    enrich_ir_metadata,
    get_grobid_client,
    parse_tei_metadata,
)
from app.pipeline.parser_backend import DocxBackend, MarkdownBackend, parse_any_document
from app.storage import Storage

# ---------------------------------------------------------------------
# Markdown 后端
# ---------------------------------------------------------------------

SAMPLE_MD = """# Attention Is All You Need

We propose the Transformer architecture.

$$Attention(Q,K,V)=softmax(QK^T/\\sqrt{d_k})V$$

The scaling factor prevents vanishing gradients.

## Experiments

| Model | BLEU |
| --- | --- |
| Base | 27.3 |

Results are strong across seeds.
"""


def test_markdown_backend_sections_and_formula(tmp_path: Path) -> None:
    path = tmp_path / "paper.md"
    path.write_text(SAMPLE_MD, encoding="utf-8")
    ir = MarkdownBackend().parse(path)

    assert ir.ok is True
    assert ir.parser == "markdown"
    titles = [title for title, _ in ir.sections]
    assert any("Attention Is All You Need" in t for t in titles)
    assert any("Experiments" in t for t in titles)
    # $$ 公式原样保留（后端不做公式识别，交给切块原子性）
    assert "$$Attention(Q,K,V)" in ir.text
    # 表格行保留
    assert "| Model | BLEU |" in ir.text


def test_markdown_backend_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.md"
    path.write_text("", encoding="utf-8")
    ir = MarkdownBackend().parse(path)
    assert ir.ok is False
    assert ir.error_kind == "empty_text"


# ---------------------------------------------------------------------
# DOCX 后端
# ---------------------------------------------------------------------


def _make_docx(path: Path) -> Path:
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Introduction", level=1)
    document.add_paragraph("This is the introduction body.")
    document.add_heading("Method", level=2)
    document.add_paragraph("We compute attention with scaled dot products.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Model"
    table.cell(0, 1).text = "Acc"
    table.cell(1, 0).text = "A"
    table.cell(1, 1).text = "91.2"
    document.save(str(path))
    return path


def test_docx_backend_headings_and_table(tmp_path: Path) -> None:
    path = _make_docx(tmp_path / "paper.docx")
    backend = DocxBackend()
    assert backend.is_available() is True
    ir = backend.parse(path)

    assert ir.ok is True
    assert ir.parser == "docx"
    titles = [title for title, _ in ir.sections]
    assert any("Introduction" in t for t in titles)
    assert any("Method" in t for t in titles)
    assert "scaled dot products" in ir.text
    assert "| Model | Acc |" in ir.text  # 表格转 Markdown
    assert any(n.type == "table" for n in ir.nodes)


def test_docx_backend_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.docx"
    path.write_bytes(b"not a docx")
    ir = DocxBackend().parse(path)
    assert ir.ok is False


# ---------------------------------------------------------------------
# FileRouter 统一分发
# ---------------------------------------------------------------------


def test_parse_any_document_dispatches_by_suffix(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# Title\n\nBody.", encoding="utf-8")
    ir_md = parse_any_document(md)
    assert ir_md.ok is True and ir_md.parser == "markdown"

    docx_path = _make_docx(tmp_path / "doc.docx")
    ir_docx = parse_any_document(docx_path)
    assert ir_docx.ok is True and ir_docx.parser == "docx"

    bad = tmp_path / "doc.txt"
    bad.write_text("plain", encoding="utf-8")
    ir_bad = parse_any_document(bad)
    assert ir_bad.ok is False
    assert "不支持的文件类型" in ir_bad.error


def test_parse_document_entrypoint(tmp_path: Path) -> None:
    md = tmp_path / "entry.md"
    md.write_text("# 标题\n\n正文内容。", encoding="utf-8")
    ir = parse_document(md)
    assert ir.ok is True
    assert ir.parser == "markdown"


def test_storage_save_upload_keeps_suffix(tmp_path: Path) -> None:
    import io as _io

    from fastapi import UploadFile

    storage = Storage(base_dir=tmp_path / "data", templates_dir=tmp_path / "templates")

    upload = UploadFile(filename="notes.md", file=_io.BytesIO(b"# Hello\n\nWorld"))
    storage.save_upload("paper-md", upload, source_filename="notes.md")
    assert storage.source_path("paper-md").name == "source.md"

    upload2 = UploadFile(filename="thesis.docx", file=_io.BytesIO(b"fake-docx"))
    storage.save_upload("paper-docx", upload2, source_filename="thesis.docx")
    assert storage.source_path("paper-docx").name == "source.docx"

    # 旧 PDF 行为不变
    upload3 = UploadFile(filename="paper.pdf", file=_io.BytesIO(b"%PDF-fake"))
    storage.save_upload("paper-pdf", upload3, source_filename="paper.pdf")
    assert storage.source_path("paper-pdf").name == "source.pdf"
    assert storage.pdf_path("paper-pdf").name == "source.pdf"


# ---------------------------------------------------------------------
# GROBID 元数据增强
# ---------------------------------------------------------------------

TEI_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>Attention Is All You Need</title>
        <author><persName><forename>Ashish</forename><surname>Vaswani</surname></persName></author>
        <author><persName><forename>Noam</forename><surname>Shazeer</surname></persName></author>
      </titleStmt>
      <sourceDesc>
        <biblStruct>
          <idno type="DOI">10.5555/3295222</idno>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract><p>We propose the Transformer.</p></abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <listBibl>
        <biblStruct><title>Sequence to Sequence Learning</title></biblStruct>
        <biblStruct><title>Layer Normalization</title></biblStruct>
      </listBibl>
    </body>
  </text>
</TEI>
"""


def test_parse_tei_metadata_fields() -> None:
    meta = parse_tei_metadata(TEI_FIXTURE)
    assert meta["title"] == "Attention Is All You Need"
    assert meta["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert "Transformer" in meta["abstract"]
    assert meta["doi"] == "10.5555/3295222"
    assert len(meta["references"]) == 2


def test_parse_tei_metadata_invalid_xml() -> None:
    assert parse_tei_metadata("<not xml") == {}


def test_get_grobid_client_gating() -> None:
    assert get_grobid_client(SimpleNamespace()) is None
    assert get_grobid_client(SimpleNamespace(grobid_enabled=True)) is not None


def test_enrich_ir_metadata_disabled_and_not_pdf(tmp_path: Path) -> None:
    ir = DocumentIR()
    enriched, note = enrich_ir_metadata(ir, tmp_path / "x.pdf", SimpleNamespace())
    assert enriched is False and note == "grobid_disabled"

    md = tmp_path / "x.md"
    md.write_text("# t", encoding="utf-8")
    enriched2, note2 = enrich_ir_metadata(ir, md, SimpleNamespace(grobid_enabled=True))
    assert enriched2 is False and note2 == "not_pdf"


def test_enrich_ir_metadata_unreachable_server(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-fake")
    settings = SimpleNamespace(grobid_enabled=True, grobid_base_url="http://localhost:9", grobid_timeout_sec=1.0)
    ir = DocumentIR()
    with patch.object(GrobidClient, "available", return_value=False):
        enriched, note = enrich_ir_metadata(ir, pdf, settings)
    assert enriched is False
    assert note == "grobid_unreachable"
    assert ir.metadata == {}


def test_enrich_ir_metadata_success(tmp_path: Path) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-fake")
    settings = SimpleNamespace(grobid_enabled=True, grobid_base_url="http://grobid", grobid_timeout_sec=5.0)
    ir = DocumentIR(sections=[("## 1 引言", "正文")])
    with (
        patch.object(GrobidClient, "available", return_value=True),
        patch.object(GrobidClient, "extract_tei", return_value=TEI_FIXTURE),
    ):
        enriched, note = enrich_ir_metadata(ir, pdf, settings)
    assert enriched is True and note == "ok"
    assert ir.metadata["title"] == "Attention Is All You Need"
    assert ir.metadata["doi"] == "10.5555/3295222"


def test_orchestrator_load_ir_enriches_via_grobid(tmp_path: Path) -> None:
    """_load_ir 在解析成功后调用 GROBID 增强（grobid 配置开启时）。"""
    from tests.helpers import MockStorage

    from app.harness.pipeline.orchestrator import _load_ir

    storage = MockStorage(tmp_path)
    settings = SimpleNamespace(grobid_enabled=True, grobid_base_url="http://grobid")
    ir = DocumentIR(parser="pymupdf", sections=[("## 1 引言", "正文")])

    calls: list[DocumentIR] = []

    def fake_enrich(target_ir, path, cfg):
        calls.append(target_ir)
        target_ir.metadata["title"] = "Enriched Title"
        return True, "ok"

    with (
        patch("app.harness.pipeline.orchestrator.parse_document", return_value=ir),
        patch("app.pipeline.grobid.enrich_ir_metadata", side_effect=fake_enrich),
    ):
        loaded = _load_ir("paper-g", storage, settings)

    assert calls and calls[0] is ir
    assert loaded.metadata["title"] == "Enriched Title"


# ---------------------------------------------------------------------
# 确定性 chunk ID
# ---------------------------------------------------------------------


def test_chunk_id_is_content_deterministic() -> None:
    from app.storage import VectorStore

    id_a1 = VectorStore._chunk_id("p1", 0, "same content")
    id_a2 = VectorStore._chunk_id("p1", 0, "same content")
    id_b = VectorStore._chunk_id("p1", 0, "different content")
    id_index = VectorStore._chunk_id("p1", 1, "same content")

    assert id_a1 == id_a2  # 同内容同索引 -> 同 ID
    assert id_a1 != id_b  # 内容变化 -> ID 变化
    assert id_a1 != id_index  # 索引变化 -> ID 变化


def test_upsert_chunks_uses_content_hashed_ids(tmp_path: Path) -> None:
    """两次 upsert 相同内容生成完全相同的 ID（幂等）。"""
    from app.storage import VectorStore

    class FakeCollection:
        def __init__(self) -> None:
            self.recorded_ids: list[list[str]] = []

        def delete(self, where):
            pass

        def add(self, **kwargs: object) -> None:
            self.recorded_ids.append(list(kwargs["ids"]))  # type: ignore[arg-type]

    class FakeEmbedder:
        def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[list[float]]:
            return [[0.1] for _ in texts]

    store = VectorStore(
        base_dir=tmp_path,
        db_subdir="vd",
        provider="chromadb",
        collection_name="c",
        embedding_model_name="fake",
    )
    store._ready = True
    store._collection = FakeCollection()
    store._embedder = FakeEmbedder()

    store.upsert_chunks("p1", ["chunk text one", "chunk text two"])
    store.upsert_chunks("p1", ["chunk text one", "chunk text two"])  # 重跑（delete+add）

    recorded = store._collection.recorded_ids
    assert len(recorded) == 2
    assert recorded[0] == recorded[1]  # 幂等：内容相同 -> ID 相同
    assert len(set(recorded[0])) == 2  # 不同内容 -> 不同 ID
