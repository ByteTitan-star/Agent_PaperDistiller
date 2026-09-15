"""解析管线测试：Preflight / PyMuPDF 后端 / ParserRouter 降级 / 结构感知分块 / MinerU 映射 / 公式链路 / OCR。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF 未安装时跳过解析引擎测试")

from app.pipeline.document_ir import PreflightReport
from app.pipeline.document_parser import (
    chunk_sections_with_meta,
    parse_pdf_document,
    split_content_units,
)
from app.pipeline.parser_backend import (
    MinerUBackend,
    PaddleOCRBackend,
    ParserRouter,
    PyMuPDFBackend,
    PypdfBackend,
    _rows_to_markdown,
    is_formula_text,
    math_glyph_ratio,
)
from app.pipeline.preflight import preflight_pdf

# ---------------------------------------------------------------------
# 测试 PDF 生成工具
# ---------------------------------------------------------------------


def make_two_column_pdf(path: Path) -> Path:
    """双栏论文样例：左栏 Introduction/Method，右栏 Experiments/Conclusion/REFERENCES。"""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    mid = 612 / 2

    def put(x: float, y: float, text: str, size: float = 10, bold: bool = False) -> None:
        page.insert_text((x, y), text, fontsize=size, fontname="hebo" if bold else "helv")

    put(72, 72, "A Study of Attention Mechanisms", size=16, bold=True)
    put(72, 100, "1. Introduction", size=12, bold=True)
    put(72, 120, "Attention models improve over RNNs. We study soft-")
    put(72, 132, "ware attention patterns in long documents and re-")
    put(72, 144, "port strong gains on translation benchmarks.")
    put(72, 168, "2. Method", size=12, bold=True)
    put(72, 188, "We compute attention with scaled dot products and")
    put(72, 200, "stack layers with residual connections everywhere.")
    put(mid + 20, 120, "3. Experiments", size=12, bold=True)
    put(mid + 20, 140, "We evaluate on benchmark datasets and report")
    put(mid + 20, 152, "results across multiple seeds and epochs with")
    put(mid + 20, 164, "careful hyperparameter tuning for all baselines.")
    put(mid + 20, 188, "4. Conclusion", size=12, bold=True)
    put(mid + 20, 208, "Softmax attention wins consistently. Future work")
    put(mid + 20, 220, "remains on efficient sparse attention variants.")
    put(mid + 20, 244, "REFERENCES", size=12, bold=True)
    put(mid + 20, 264, "[1] Vaswani et al. Attention is all you need. 2017.")
    doc.save(str(path))
    return path


def make_scanned_pdf(path: Path) -> Path:
    """无文本层的"扫描件"：整页仅一张图片。"""
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 600, 800))
    pix.clear_with(255)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(str(path))
    return path


def make_simple_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    page.insert_text((72, 120), "This paper studies attention mechanisms in depth.")
    page.insert_text((72, 140), "We propose a novel training pipeline for models.")
    doc.save(str(path))
    return path


# ---------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------


def test_preflight_native_pdf(tmp_path: Path) -> None:
    report = preflight_pdf(make_two_column_pdf(tmp_path / "native.pdf"))
    assert report.parser_route == "native"
    assert report.has_text_layer is True
    assert report.encrypted is False
    assert report.page_count == 1
    assert report.char_count > 100


def test_preflight_scanned_pdf(tmp_path: Path) -> None:
    report = preflight_pdf(make_scanned_pdf(tmp_path / "scan.pdf"))
    assert report.parser_route == "scanned"
    assert report.has_text_layer is False
    assert report.scanned_ratio >= 0.99
    assert any("扫描件" in w for w in report.warnings)


def test_preflight_missing_file_is_fallback(tmp_path: Path) -> None:
    report = preflight_pdf(tmp_path / "ghost.pdf")
    assert report.parser_route == "fallback"


# ---------------------------------------------------------------------
# PyMuPDF 后端
# ---------------------------------------------------------------------


def test_pymupdf_two_column_reading_order(tmp_path: Path) -> None:
    ir = PyMuPDFBackend().parse(make_two_column_pdf(tmp_path / "col.pdf"))
    assert ir.ok is True
    titles = [title for title, _ in ir.sections]
    # 双栏页：左栏章节先于右栏章节（阅读顺序正确）
    intro_idx = next(i for i, t in enumerate(titles) if "引言" in t)
    exp_idx = next(i for i, t in enumerate(titles) if "实验" in t)
    method_idx = next(i for i, t in enumerate(titles) if "方法" in t)
    assert intro_idx < method_idx < exp_idx


def test_pymupdf_dehyphenation(tmp_path: Path) -> None:
    ir = PyMuPDFBackend().parse(make_two_column_pdf(tmp_path / "hyph.pdf"))
    intro = next(content for title, content in ir.sections if "引言" in title)
    assert "software attention" in intro  # "soft-\nware" 已修复
    assert "soft- ware" not in intro


def test_pymupfd_references_nodes(tmp_path: Path) -> None:
    ir = PyMuPDFBackend().parse(make_two_column_pdf(tmp_path / "ref.pdf"))
    refs = [n for n in ir.nodes if n.type == "reference"]
    assert len(refs) == 1
    assert "Vaswani" in refs[0].text


def test_pymupdf_page_markers(tmp_path: Path) -> None:
    ir = PyMuPDFBackend().parse(make_two_column_pdf(tmp_path / "pg.pdf"))
    assert "[Page 1]" in ir.text


def test_pymupdf_corrupt_file_returns_failed_ir(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    ir = PyMuPDFBackend().parse(bad)
    assert ir.ok is False
    assert ir.error_kind in {"corrupt_pdf", "empty_text"}


# ---------------------------------------------------------------------
# ParserRouter
# ---------------------------------------------------------------------


def test_router_parses_native_pdf(tmp_path: Path) -> None:
    ir = ParserRouter().parse(make_simple_pdf(tmp_path / "s.pdf"))
    assert ir.ok is True
    assert ir.parser == "pymupdf"
    assert ir.sections


def test_router_scanned_pdf_without_ocr_fails_clearly(tmp_path: Path) -> None:
    """扫描件且未启用 OCR/MinerU：明确失败（error_kind=scanned_pdf），而不是错误文案当正文。"""
    ir = ParserRouter().parse(make_scanned_pdf(tmp_path / "scan.pdf"))
    assert ir.ok is False
    assert ir.error_kind == "scanned_pdf"
    assert "MinerU" in ir.error or "OCR" in ir.error


def test_router_falls_back_to_pypdf(tmp_path: Path) -> None:
    """PyMuPDF 崩溃时自动降级 pypdf。"""
    pdf = make_simple_pdf(tmp_path / "fallback.pdf")
    with patch.object(PyMuPDFBackend, "parse", side_effect=RuntimeError("boom")):
        ir = ParserRouter().parse(pdf)
    assert ir.ok is True
    assert ir.parser == "pypdf"


def test_router_explicit_pypdf_backend(tmp_path: Path) -> None:
    settings = SimpleNamespace(parser_backend="pypdf")
    ir = ParserRouter(settings).parse(make_simple_pdf(tmp_path / "p.pdf"))
    assert ir.parser == "pypdf"


def test_parse_pdf_document_entrypoint(tmp_path: Path) -> None:
    ir = parse_pdf_document(make_simple_pdf(tmp_path / "entry.pdf"))
    assert ir.ok is True
    assert any("引言" in title for title, _ in ir.sections)


def test_pypdf_backend_scanned_returns_failed(tmp_path: Path) -> None:
    ir = PypdfBackend().parse(make_scanned_pdf(tmp_path / "scan.pdf"))
    assert ir.ok is False
    assert ir.error_kind == "scanned_pdf"


# ---------------------------------------------------------------------
# 结构感知分块
# ---------------------------------------------------------------------


def test_split_content_units_formula_atomic() -> None:
    content = "前文说明。\n\n$$Attention(Q,K,V)=softmax(QK^T/\\sqrt{d_k})V$$\n\n后文说明。"
    units = split_content_units(content)
    types = [t for _, t in units]
    assert types.count("formula") == 1
    formula = next(text for text, t in units if t == "formula")
    assert formula.startswith("$$") and formula.endswith("$$")


def test_split_content_units_table_grouping() -> None:
    content = "结果如下：\n\n| Model | Acc |\n| --- | --- |\n| A | 91.2 |\n| B | 94.5 |\n\n结论段落。"
    units = split_content_units(content)
    table = next(text for text, t in units if t == "table")
    assert table.count("\n") == 3  # 表头 + 分隔 + 两行数据


def test_chunk_sections_formula_never_split() -> None:
    long_formula = "$$" + "x+" * 300 + "y$$"
    sections = [("## 3 方法", f"定义如下：{long_formula}")]
    chunks, metas = chunk_sections_with_meta(sections, chunk_size=100, overlap=10)
    formula_chunks = [c for c in chunks if "$$" in c]
    assert len(formula_chunks) == 1  # 公式只出现在一个块中，未被切断
    formula_chunk = formula_chunks[0]
    assert formula_chunk.strip().endswith("$$")
    assert formula_chunk.count("$$") == 2  # 完整的一对 $$...$$
    assert metas[1]["element_type"] == "formula"


def test_chunk_sections_oversized_table_repeats_header() -> None:
    rows = "\n".join(f"| M{i} | {i} |" for i in range(80))
    table = "| Model | Score |\n| --- | --- |\n" + rows
    sections = [("## 4 实验", table)]
    chunks, metas = chunk_sections_with_meta(sections, chunk_size=300, overlap=0)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert "| Model | Score |" in chunk  # 每个子块都带表头
    assert all(m["element_type"] == "table" for m in metas)


def test_chunk_sections_reference_meta() -> None:
    sections = [
        ("## 参考文献", "[1] Some reference text. " * 20),
        ("## 5 结论", "结论正文。" * 100),
    ]
    chunks, metas = chunk_sections_with_meta(sections, chunk_size=200, overlap=0)
    ref_metas = [m for m in metas if m["is_reference"]]
    assert ref_metas and all(m["element_type"] == "reference" for m in ref_metas)
    text_metas = [m for m in metas if not m["is_reference"]]
    assert text_metas and all(m["element_type"] == "text" for m in text_metas)


def test_chunk_sections_keeps_section_structure() -> None:
    sections = [
        ("## 1 引言", "第一段。" * 50),
        ("## 2 方法", "第二段。" * 50),
    ]
    chunks, metas = chunk_sections_with_meta(sections, chunk_size=200, overlap=0)
    assert chunks[0].startswith("## 1 引言")
    # 每个块只属于一个章节，且块内携带该章节标题
    for chunk, meta in zip(chunks, metas, strict=False):
        assert meta["section"] in {"1 引言", "2 方法"}
        assert meta["section"] in chunk


def test_rows_to_markdown_alignment() -> None:
    md = _rows_to_markdown([["Model", "Acc"], ["A", None], ["B", "94.5"]])
    lines = md.splitlines()
    assert lines[0] == "| Model | Acc |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| A |  |"
    assert lines[3] == "| B | 94.5 |"


# ---------------------------------------------------------------------
# MinerU 适配器（映射逻辑 + CLI 失败路径）
# ---------------------------------------------------------------------


def _mineru_entries() -> list[dict]:
    return [
        {"type": "title", "text": "1 Introduction", "level": 1, "page_idx": 0},
        {"type": "text", "text": "We study attention.", "page_idx": 0},
        {"type": "equation", "latex": "E=mc^2", "page_idx": 0},
        {"type": "table", "html": "<table><tr><td>1</td></tr></table>", "page_idx": 1},
        {"type": "image", "img_path": "images/a.jpg", "page_idx": 1},
        {"type": "image_caption", "text": "Figure 1: An image", "page_idx": 1},
    ]


def test_mineru_entries_to_ir_mapping() -> None:
    pdf = make_simple_pdf(Path("/tmp/mineru_map.pdf"))
    ir = MinerUBackend()._entries_to_ir(_mineru_entries(), pdf, PreflightReport(page_count=2))
    assert ir.ok is True
    assert ir.parser == "mineru"

    eq = [n for n in ir.nodes if n.type == "equation"]
    assert eq and eq[0].latex == "E=mc^2"
    tbl = [n for n in ir.nodes if n.type == "table"]
    assert tbl and "<table>" in tbl[0].html
    fig = [n for n in ir.nodes if n.type == "figure"]
    assert fig and fig[0].meta["img_path"] == "images/a.jpg"
    # image_caption 绑定到最近的前一个 figure 节点
    assert any(n.type == "figure" and n.caption == "Figure 1: An image" for n in ir.nodes)
    assert any(n.type == "heading" and "Introduction" in n.text for n in ir.nodes)
    assert "$$E=mc^2$$" in ir.text
    assert "[Page 1]" in ir.text


def test_mineru_cli_failure_returns_failed_ir(tmp_path: Path) -> None:
    pdf = make_simple_pdf(tmp_path / "m.pdf")
    completed = SimpleNamespace(returncode=1, stdout="", stderr="model load error")

    with (
        patch("app.pipeline.parser_backend.shutil.which", return_value="/usr/local/bin/mineru"),
        patch("app.pipeline.parser_backend.subprocess.run", return_value=completed),
    ):
        ir = MinerUBackend().parse(pdf)

    assert ir.ok is False
    assert ir.error_kind == "corrupt_pdf"
    assert "model load error" in ir.error


def test_mineru_unavailable_when_cli_missing(tmp_path: Path) -> None:
    with patch("app.pipeline.parser_backend.shutil.which", return_value=None):
        assert MinerUBackend().is_available() is False


# ---------------------------------------------------------------------
# 公式链路（数学行检测 -> 裁剪 -> 识别 -> $$LaTeX$$ 回填）
# ---------------------------------------------------------------------


class FakeFormulaRecognizer:
    """公式识别假实现：记录收到的 PNG 字节，返回固定 LaTeX。"""

    name = "fake"

    def __init__(self, latex: str = "E = m c^{2}") -> None:
        self.latex = latex
        self.calls: list[bytes] = []

    def recognize(self, png_bytes: bytes) -> str | None:
        self.calls.append(png_bytes)
        return self.latex


def make_formula_pdf(path: Path) -> Path:
    """含数学字形密集行（Latin-1 数学符号）的单栏 PDF。"""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    page.insert_text((72, 120), "The loss function of the model is defined as follows.", fontsize=10)
    page.insert_text((72, 150), "±×÷² ±×÷² ±×÷² ±×÷²", fontsize=10)  # 数学字形密集行（公式区域）
    page.insert_text((72, 180), "where m is the mass and c is the speed of light.", fontsize=10)
    doc.save(str(path))
    return path


def test_math_glyph_ratio_heuristics() -> None:
    assert math_glyph_ratio("") == 0.0
    assert math_glyph_ratio("±×÷² ±×÷²") > 0.5
    assert math_glyph_ratio("normal english sentence") == 0.0
    # 单个数学字符的普通句子不应判定为公式行
    assert is_formula_text("the value of α is small in practice") is False
    assert is_formula_text("±×÷² ±×÷² ±×÷²") is True


def test_formula_recognition_fills_latex(tmp_path: Path) -> None:
    """注入识别器后：公式行被替换为 $$LaTeX$$，并产出 equation 节点。"""
    recognizer = FakeFormulaRecognizer(latex="E = m c^{2}")
    backend = PyMuPDFBackend(formula_recognizer=recognizer)
    ir = backend.parse(make_formula_pdf(tmp_path / "formula.pdf"))

    assert ir.ok is True
    assert recognizer.calls, "识别器应至少收到一次裁剪图片"
    assert all(isinstance(png, bytes) and png[:4] == b"\x89PNG" for png in recognizer.calls)
    assert "$$E = m c^{2}$$" in ir.text  # LaTeX 回填正文
    assert "±×÷²" not in ir.text  # 原始数学字形不再流入正文
    equations = [n for n in ir.nodes if n.type == "equation"]
    assert len(equations) == 1
    assert equations[0].latex == "E = m c^{2}"
    assert equations[0].page == 1
    assert equations[0].bbox  # 带区域坐标


def test_formula_recognition_off_keeps_original_glyphs(tmp_path: Path) -> None:
    """默认（formula_backend=off）：保留原始字形文本，行为与旧版一致。"""
    backend = PyMuPDFBackend()  # 未注入识别器
    ir = backend.parse(make_formula_pdf(tmp_path / "formula_off.pdf"))
    assert ir.ok is True
    assert "±×÷²" in ir.text
    assert not [n for n in ir.nodes if n.type == "equation"]


def test_formula_recognition_failure_degrades_gracefully(tmp_path: Path) -> None:
    """识别失败（返回 None）：优雅降级，保留原始字形。"""

    class FailingRecognizer:
        name = "fail"

        def recognize(self, png_bytes: bytes) -> str | None:
            return None

    backend = PyMuPDFBackend(formula_recognizer=FailingRecognizer())
    ir = backend.parse(make_formula_pdf(tmp_path / "formula_fail.pdf"))
    assert ir.ok is True
    assert "±×÷²" in ir.text
    assert not [n for n in ir.nodes if n.type == "equation"]


def test_router_injects_formula_recognizer(tmp_path: Path) -> None:
    """formula_backend=mathpix 配置缺失时路由器注入 None（off 语义）。"""
    settings = SimpleNamespace(formula_backend="off")
    router = ParserRouter(settings)
    assert router._formula_recognizer is None


def test_formula_chunk_is_atomic_after_recognition(tmp_path: Path) -> None:
    """回填的 $$...$$ 在切块时保持原子（公式链路与结构感知分块衔接）。"""
    recognizer = FakeFormulaRecognizer(latex="\\sum_{i=1}^{n} w_i x_i + b")
    ir = PyMuPDFBackend(formula_recognizer=recognizer).parse(make_formula_pdf(tmp_path / "chunk.pdf"))
    chunks, metas = chunk_sections_with_meta(ir.sections, 120, 0)
    formula_chunks = [c for c in chunks if "$$" in c]
    assert len(formula_chunks) == 1
    assert formula_chunks[0].count("$$") == 2
    assert any(m["element_type"] == "formula" for m in metas)


# ---------------------------------------------------------------------
# PaddleOCR 扫描件通道（fake 引擎）
# ---------------------------------------------------------------------


def _fake_paddleocr_module() -> types.ModuleType:
    module = types.ModuleType("paddleocr")

    class FakePaddleOCR:
        def __init__(self, **kwargs: object) -> None:
            pass

        def ocr(self, img: bytes, cls: bool = True):
            return [
                [
                    [[[0, 0], [10, 10]], ("OCR line one about attention", 0.98)],
                    [[[0, 20], [10, 30]], ("OCR line two about transformers", 0.97)],
                ]
            ]

    module.PaddleOCR = FakePaddleOCR
    return module


def test_paddleocr_backend_with_fake_engine(tmp_path: Path) -> None:
    fake = _fake_paddleocr_module()
    with patch.dict(sys.modules, {"paddleocr": fake}):
        backend = PaddleOCRBackend()
        assert backend.is_available() is True
        ir = backend.parse(make_scanned_pdf(tmp_path / "ocr.pdf"))

    assert ir.ok is True
    assert ir.parser == "paddleocr"
    assert "OCR line one about attention" in ir.text
    assert "[Page 1]" in ir.text


def test_paddleocr_backend_unavailable_without_module() -> None:
    with patch.dict(sys.modules, {"paddleocr": None}):
        assert PaddleOCRBackend().is_available() is False


# ---------------------------------------------------------------------
# chunk 页码元数据
# ---------------------------------------------------------------------


def test_chunk_sections_page_metadata() -> None:
    sections = [("## 1 引言", "正文内容。" * 30), ("## 2 方法", "方法内容。" * 30)]
    pages = {"## 1 引言": 1, "## 2 方法": 5}
    chunks, metas = chunk_sections_with_meta(sections, 200, 0, section_pages=pages)
    assert all(m["page"] in {1, 5} for m in metas)
    by_section = {m["section"]: m["page"] for m in metas}
    assert by_section["1 引言"] == 1
    assert by_section["2 方法"] == 5
    # 无页码映射时安全回退为 0
    _, metas_no_pages = chunk_sections_with_meta(sections, 200, 0)
    assert all(m["page"] == 0 for m in metas_no_pages)
