"""DocumentIR 数据模型与 JSON 持久化测试。"""

from __future__ import annotations

from pathlib import Path

from app.pipeline.document_ir import (
    DocNode,
    DocumentIR,
    PreflightReport,
    failed_ir,
)


def _sample_ir() -> DocumentIR:
    return DocumentIR(
        parser="pymupdf",
        text="[Page 1]\nbody",
        sections=[("## 1 引言", "引言正文"), ("## 参考文献", "[1] Ref")],
        nodes=[
            DocNode(node_id="heading-0001", type="heading", text="## 1 引言", page=1, level=2),
            DocNode(
                node_id="eq-0002",
                type="equation",
                text="$$E=mc^2$$",
                latex="E=mc^2",
                page=2,
                section_path=["1 引言"],
                bbox=[10.0, 20.0, 30.0, 40.0],
                meta={"confidence": 0.9},
            ),
        ],
        report=PreflightReport(page_count=3, scanned_ratio=0.0, char_count=8000, parser_route="native"),
    )


def test_ir_json_roundtrip(tmp_path: Path) -> None:
    ir = _sample_ir()
    path = tmp_path / "parse_artifact.json"
    ir.save(path)

    loaded = DocumentIR.load(path)
    assert loaded is not None
    assert loaded.parser == "pymupdf"
    assert loaded.sections == [("## 1 引言", "引言正文"), ("## 参考文献", "[1] Ref")]
    assert loaded.report.page_count == 3
    assert loaded.report.parser_route == "native"
    assert len(loaded.nodes) == 2
    eq = loaded.nodes[1]
    assert eq.type == "equation"
    assert eq.latex == "E=mc^2"
    assert eq.bbox == [10.0, 20.0, 30.0, 40.0]
    assert eq.meta == {"confidence": 0.9}


def test_ir_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert DocumentIR.load(tmp_path / "nope.json") is None


def test_ir_load_corrupt_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert DocumentIR.load(path) is None


def test_failed_ir_semantics() -> None:
    ir = failed_ir("scanned_pdf", "no text layer")
    assert ir.ok is False
    assert ir.error_kind == "scanned_pdf"
    assert ir.effective_chars == 0


def test_node_stats() -> None:
    stats = _sample_ir().node_stats()
    assert stats == {"heading": 1, "equation": 1}
