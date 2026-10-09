"""表格提取升级链测试：质量门禁纯函数 / 矢量低质量升级 / 检测区域升级 / 检索闭环。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF 未安装时跳过")

from app.pipeline.document_ir import DocNode, DocumentIR
from app.pipeline.parser_backend import PyMuPDFBackend
from app.pipeline.table_extraction import (
    NullTableRecognizer,
    bbox_iou,
    get_table_recognizer,
    table_quality_report,
)

# ---------------------------------------------------------------------
# 质量门禁（纯函数）
# ---------------------------------------------------------------------


def test_quality_gate_accepts_healthy_grid() -> None:
    rows = [["Model", "Acc", "F1"], ["A", "91.2", "90.1"], ["B", "94.5", "93.8"]]
    report = table_quality_report(rows)
    assert report["sane"] is True
    assert report["rows"] == 3 and report["cols"] == 3
    assert report["fill_ratio"] == 1.0 and report["col_consistency"] == 1.0


def test_quality_gate_rejects_degenerate_grids() -> None:
    assert table_quality_report(None)["reason"] == "empty"
    assert table_quality_report([])["reason"] == "empty"
    # 单行（一维碎片，find_tables 对无框线表格的典型误抽）
    assert table_quality_report([["a", "b", "c"]])["reason"] == "rows<2"
    # 单列
    assert table_quality_report([["a"], ["b"]])["reason"] == "cols<2"
    # 大面积空置
    sparse = [["h1", "h2", "h3", "h4", "h5", "h6"]] + [["x", "", "", "", "", ""]] * 5
    assert table_quality_report(sparse)["reason"].startswith("fill<")
    # 行列数参差（乱网格）
    ragged = [["a", "b", "c"], ["d"], ["e", "f"]]
    assert table_quality_report(ragged)["reason"].startswith("ragged_cols<")


def test_bbox_iou() -> None:
    a = (0, 0, 10, 10)
    assert bbox_iou(a, a) == pytest.approx(1.0)
    assert bbox_iou(a, (20, 20, 30, 30)) == 0.0
    assert 0.0 < bbox_iou(a, (5, 5, 15, 15)) < 1.0


# ---------------------------------------------------------------------
# 识别器配置门控
# ---------------------------------------------------------------------


def test_get_table_recognizer_gating() -> None:
    assert isinstance(get_table_recognizer(SimpleNamespace()), NullTableRecognizer)
    assert isinstance(get_table_recognizer(SimpleNamespace(table_recognition="off")), NullTableRecognizer)
    # vlm 档但无 key -> Null
    assert isinstance(
        get_table_recognizer(SimpleNamespace(table_recognition="vlm", qwen_api_key="your-api-key")),
        NullTableRecognizer,
    )
    recognizer = get_table_recognizer(SimpleNamespace(table_recognition="vlm", qwen_api_key="sk-x"))
    assert recognizer.name == "vlm-table"


# ---------------------------------------------------------------------
# 矢量表格低质量 -> 升级（fake 识别器）
# ---------------------------------------------------------------------


class FakeTableRecognizer:
    name = "fake-table"

    def __init__(self, markdown: str | None = "| Model | Acc |\n| --- | --- |\n| A | 91.2 |") -> None:
        self.markdown = markdown
        self.calls: list[bytes] = []

    def recognize(self, png_bytes: bytes) -> str | None:
        self.calls.append(png_bytes)
        return self.markdown


def _pdf_with_ruled_table(path: Path) -> Path:
    """带框线的 2x3 表格（矢量线 + 单元格文字），find_tables 可检出。"""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    origin_x, origin_y, cell_w, cell_h = 72, 150, 90, 24
    cols, rows = 3, 3
    end_x = origin_x + cell_w * cols
    end_y = origin_y + cell_h * rows
    for r in range(rows + 1):
        page.draw_line((origin_x, origin_y + r * cell_h), (end_x, origin_y + r * cell_h))
    for c in range(cols + 1):
        page.draw_line((origin_x + c * cell_w, origin_y), (origin_x + c * cell_w, end_y))
    data = [["Model", "Acc", "F1"], ["A", "91.2", "90.1"], ["B", "94.5", "93.8"]]
    for r, row in enumerate(data):
        for c, cell in enumerate(row):
            page.insert_text((origin_x + 4 + c * cell_w, origin_y + 16 + r * cell_h), cell, fontsize=9)
    doc.save(str(path))
    return path


def test_ruled_table_vector_source(tmp_path: Path) -> None:
    pdf = _pdf_with_ruled_table(tmp_path / "ruled.pdf")
    ir = PyMuPDFBackend().parse(pdf)
    tables = [n for n in ir.nodes if n.type == "table"]
    if not tables:
        pytest.skip("find_tables 未在该 fixture 上检出（环境差异）")
    assert tables[0].meta["source"] == "vector"
    assert tables[0].meta["sane"] is True
    assert "| Model | Acc | F1 |" in tables[0].text
    # 全文回填（检索/翻译可见）
    assert "| Model | Acc | F1 |" in ir.text


def test_low_quality_vector_table_escalates(tmp_path: Path, monkeypatch) -> None:
    """门禁失败的矢量表格升级识别：source=model，Markdown 来自识别器。"""
    pdf = _pdf_with_ruled_table(tmp_path / "lowq.pdf")
    recognizer = FakeTableRecognizer()

    import app.pipeline.parser_backend as pb

    original = pb.table_quality_report
    monkeypatch.setattr(
        pb, "table_quality_report", lambda rows, **kw: {**original(rows, **kw), "sane": False, "reason": "forced"}
    )

    backend = PyMuPDFBackend(table_recognizer=recognizer)
    ir = backend.parse(pdf)
    tables = [n for n in ir.nodes if n.type == "table"]
    if not tables:
        pytest.skip("find_tables 未检出")
    assert recognizer.calls, "低质量表格应触发识别器"
    assert tables[0].meta["source"] == "model"
    assert "| A | 91.2 |" in tables[0].text


def test_low_quality_without_recognizer_keeps_tagged(tmp_path: Path, monkeypatch) -> None:
    """无识别器时保留矢量结果并标记 vector_low_quality（不丢数据）。"""
    pdf = _pdf_with_ruled_table(tmp_path / "nolowq.pdf")

    import app.pipeline.parser_backend as pb

    original = pb.table_quality_report
    monkeypatch.setattr(
        pb, "table_quality_report", lambda rows, **kw: {**original(rows, **kw), "sane": False, "reason": "forced"}
    )

    ir = PyMuPDFBackend().parse(pdf)
    tables = [n for n in ir.nodes if n.type == "table"]
    if not tables:
        pytest.skip("find_tables 未检出")
    assert tables[0].meta["source"] == "vector_low_quality"


# ---------------------------------------------------------------------
# 检测器 table 区域 -> 升级（fake 检测器 + fake 识别器）
# ---------------------------------------------------------------------


class FakeLayoutWithTable:
    """返回一个覆盖页面中部区域的 table 检测（该处无矢量表格）。"""

    name = "fake-layout"

    def __init__(self, box_px: tuple[float, float, float, float]) -> None:
        self.box = box_px
        self.detect_calls = 0

    def detect(self, image_rgb):
        import app.pipeline.layout_detector as ld

        self.detect_calls += 1
        return [
            ld.RegionDetection(bbox=self.box, label="table", score=0.95),
        ]


def test_detected_table_region_escalates_to_model(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    # 页面中部放一张图片（模拟图片型表格，无矢量线、无文本层内容）
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 160))
    pix.clear_with(230)
    page.insert_image(pymupdf.Rect(72, 150, 420, 280), pixmap=pix)
    pdf = tmp_path / "img_table.pdf"
    doc.save(str(pdf))

    zoom = 2.0  # _mark_formula_lines_with_detector 内部渲染倍率
    table_box_px = (72 * zoom, 150 * zoom, 420 * zoom, 280 * zoom)
    recognizer = FakeTableRecognizer()
    backend = PyMuPDFBackend(
        layout_detector=FakeLayoutWithTable(table_box_px),
        table_recognizer=recognizer,
    )
    ir = backend.parse(pdf)

    assert recognizer.calls, "未覆盖的 table 检测区域应触发识别器"
    tables = [n for n in ir.nodes if n.type == "table"]
    assert len(tables) == 1
    assert tables[0].meta["source"] == "model" and tables[0].meta["escalated"] is True
    assert tables[0].page == 1
    assert "| A | 91.2 |" in ir.text  # 全文回填


def test_detected_table_covered_by_vector_skipped(tmp_path: Path) -> None:
    """检测区域与矢量表格高 IoU 重叠时不重复升级。"""
    pdf = _pdf_with_ruled_table(tmp_path / "covered.pdf")
    zoom = 2.0
    table_box_px = (70 * zoom, 145 * zoom, 345 * zoom, 230 * zoom)  # 覆盖矢量表格区域
    recognizer = FakeTableRecognizer()
    backend = PyMuPDFBackend(
        layout_detector=FakeLayoutWithTable(table_box_px),
        table_recognizer=recognizer,
    )
    ir = backend.parse(pdf)
    tables = [n for n in ir.nodes if n.type == "table"]
    if not tables:
        pytest.skip("find_tables 未检出")
    assert len(tables) == 1  # 只有一个（矢量），未重复升级
    assert tables[0].meta["source"] == "vector"


def test_detected_table_without_recognizer_warns(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    pdf = tmp_path / "warn.pdf"
    doc.save(str(pdf))

    zoom = 2.0
    backend = PyMuPDFBackend(layout_detector=FakeLayoutWithTable((100 * zoom, 150 * zoom, 400 * zoom, 250 * zoom)))
    ir = backend.parse(pdf)
    assert ir.ok is True
    assert any("未启用升级识别" in w for w in ir.report.warnings)
    assert not [n for n in ir.nodes if n.type == "table"]


# ---------------------------------------------------------------------
# 检索闭环：to_markdown 表格节 + orchestrator 表格 chunk
# ---------------------------------------------------------------------


def test_to_markdown_includes_tables_section() -> None:
    ir = DocumentIR(
        sections=[("## 1 引言", "正文。")],
        nodes=[
            DocNode(
                node_id="t1",
                type="table",
                text="| a | b |",
                page=2,
                meta={"source": "model"},
                caption="Table 1: Results",
            ),
            DocNode(node_id="t2", type="table", text="| c | d |", page=3, meta={"source": "vector"}),
        ],
    )
    md = ir.to_markdown()
    assert "## 表格" in md
    assert "Table 1: Results" in md and "| a | b |" in md
    assert "表格（第 3 页，vector）" in md and "| c | d |" in md


@pytest.mark.asyncio
async def test_orchestrator_appends_table_chunks(tmp_path):
    from unittest.mock import patch

    from tests.helpers import MockStorage

    from app.harness.pipeline.orchestrator import run_parse_step
    from app.pipeline.state_broker import TaskBroker

    storage = MockStorage(tmp_path)
    broker = TaskBroker()
    await broker.create("t-tbl", "p-tbl")
    ir = DocumentIR(
        parser="pymupdf",
        sections=[("## 1 引言", "正文内容。")],
        nodes=[
            DocNode(node_id="h1", type="heading", text="## 1 引言", page=1, level=2),
            DocNode(
                node_id="t1",
                type="table",
                text="| a | b |\n| --- | --- |\n| 1 | 2 |",
                page=2,
                meta={"source": "model"},
                caption="Table 1",
            ),
        ],
    )

    with patch("app.harness.pipeline.orchestrator.parse_document", return_value=ir):
        result = await run_parse_step(
            paper_id="p-tbl",
            task_id="t-tbl",
            storage=storage,
            broker=broker,
            settings=SimpleNamespace(max_chunk_chars=400, chunk_overlap=30),
        )

    assert result["ok"] is True
    chunks = storage.chunks["p-tbl"]
    metas = storage.chunk_metas["p-tbl"]
    table_chunks = [c for c in chunks if c.startswith("[表格")]
    assert len(table_chunks) == 1 and "Table 1" in table_chunks[0] and "| a | b |" in table_chunks[0]
    assert metas[-1]["element_type"] == "table" and metas[-1]["page"] == 2
