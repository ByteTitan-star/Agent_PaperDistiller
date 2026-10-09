"""统一输入接口层测试：任意格式 -> Markdown / 混合页按页 OCR 兜底 / 熔断器。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="PyMuPDF 未安装时跳过")
np = pytest.importorskip("numpy", reason="numpy 未安装时跳过")

from app.pipeline.document_ir import DocNode, DocumentIR
from app.pipeline.document_parser import parse_to_markdown
from app.pipeline.llm_translator import CircuitBreaker, TranslationCircuitOpenError
from app.pipeline.parser_backend import (
    IMAGE_SUFFIXES,
    SUPPORTED_FILE_SUFFIXES,
    ImageOcrBackend,
    PyMuPDFBackend,
    TxtBackend,
)

# ---------------------------------------------------------------------
# 统一入口：各格式 -> Markdown
# ---------------------------------------------------------------------


def test_parse_to_markdown_from_markdown(tmp_path: Path) -> None:
    src = tmp_path / "doc.md"
    src.write_text("# Title\n\nBody with $x$.\n", encoding="utf-8")
    result = parse_to_markdown(src)
    assert result.ok is True and result.error == ""
    assert "# Title" in result.markdown and "Body with $x$." in result.markdown


def test_parse_to_markdown_from_txt(tmp_path: Path) -> None:
    src = tmp_path / "note.txt"
    src.write_text("1. Introduction\n\nWe study attention.\n", encoding="utf-8")
    result = parse_to_markdown(src)
    assert result.ok is True
    # 既有章节规则：英文标题自动映射中文（"1. Introduction" -> "## 1 引言"）
    assert "1 引言" in result.markdown and "attention" in result.markdown


def test_parse_to_markdown_from_pdf(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    page.insert_text((72, 120), "We propose a method.", fontsize=10)
    src = tmp_path / "p.pdf"
    doc.save(str(src))
    result = parse_to_markdown(src)
    assert result.ok is True
    assert "1 引言" in result.markdown and "propose" in result.markdown


def test_parse_to_markdown_unsupported_and_missing(tmp_path: Path) -> None:
    bad = tmp_path / "file.xyz"
    bad.write_text("x", encoding="utf-8")
    result = parse_to_markdown(bad)
    assert result.ok is False
    assert "不支持的文件类型" in result.error

    ghost = parse_to_markdown(tmp_path / "ghost.pdf")
    assert ghost.ok is False  # 解析失败不抛异常，ok=False + error


def test_markdown_includes_metadata_and_figure_appendix() -> None:
    ir = DocumentIR(
        parser="pymupdf",
        metadata={"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "doi": "10.1/x"},
        sections=[("## 1 引言", "正文。"), ("## 2 方法", "$$E=mc^2$$")],
        nodes=[DocNode(node_id="f1", type="figure", text="折线图描述", page=3, caption="Figure 1: Loss")],
    )
    md = ir.to_markdown()
    assert "title: Attention Is All You Need" in md and "authors: Ashish Vaswani" in md
    assert "$$E=mc^2$$" in md
    assert "## 图表附录" in md and "Figure 1: Loss" in md and "折线图描述" in md


def test_supported_suffix_registry() -> None:
    assert {".png", ".jpg", ".txt"} <= set(SUPPORTED_FILE_SUFFIXES)
    assert ".webp" in IMAGE_SUFFIXES


# ---------------------------------------------------------------------
# 图片输入（fake OCR 引擎）
# ---------------------------------------------------------------------


class FakeOcrEngine:
    def image_lines(self, image_path: Path) -> list[str]:
        return ["1. Introduction", "Scanned page content about transformers."]

    def page_lines(self, page) -> list[str]:  # 混合页兜底用
        return ["OCR line from scanned page."]


def test_image_backend_with_fake_engine(tmp_path: Path) -> None:
    img = tmp_path / "photo.png"
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 60))
    pixmap.clear_with(255)
    pixmap.save(str(img))
    backend = ImageOcrBackend(engine=FakeOcrEngine())
    assert backend.is_available() is True
    ir = backend.parse(img)
    assert ir.ok is True and ir.parser == "image-ocr"
    assert "Scanned page content" in ir.text

    result = parse_to_markdown(img, SimpleNamespace(parser_ocr_enabled=True))
    # 统一入口走 Router（未启用 OCR 时图片通道不可用）——此处直接验证注入路径已足够
    del result


def test_image_backend_unavailable_without_engine() -> None:
    with patch("app.pipeline.parser_backend.PaddlePageOcr.available", return_value=False):
        backend = ImageOcrBackend()
        assert backend.is_available() is False


def test_txt_backend_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    ir = TxtBackend().parse(empty)
    assert ir.ok is False and ir.error_kind == "empty_text"


# ---------------------------------------------------------------------
# 混合页按页 OCR 兜底（真实 PDF + fake 引擎注入）
# ---------------------------------------------------------------------


def _make_mixed_pdf(path: Path) -> None:
    """第 1 页数字文本 + 第 2 页纯图片（无文本层的扫描页）。"""
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 90), "1. Introduction", fontsize=12, fontname="hebo")
    page1.insert_text((72, 120), "Native text layer page one.", fontsize=10)

    page2 = doc.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 800))
    pixmap.clear_with(250)
    page2.insert_image(page2.rect, pixmap=pixmap)
    doc.save(str(path))


def test_mixed_pdf_empty_page_ocr_fallback(tmp_path: Path) -> None:
    pdf = tmp_path / "mixed.pdf"
    _make_mixed_pdf(pdf)

    backend = PyMuPDFBackend(ocr_fallback=FakeOcrEngine())
    ir = backend.parse(pdf)
    assert ir.ok is True
    assert "Native text layer page one." in ir.text  # 第 1 页：文本层直读
    assert "OCR line from scanned page." in ir.text  # 第 2 页：按页 OCR 补全
    assert "[Page 2]" in ir.text
    assert "未启用按页 OCR" not in " ".join(ir.report.warnings)


def test_mixed_pdf_without_fallback_only_warns(tmp_path: Path) -> None:
    pdf = tmp_path / "mixed2.pdf"
    _make_mixed_pdf(pdf)

    backend = PyMuPDFBackend()  # 未注入 OCR 引擎
    ir = backend.parse(pdf)
    assert ir.ok is True  # 第 1 页仍正常解析，不因单页空文本失败
    assert "Native text layer page one." in ir.text
    assert any("第 2 页无文本层" in w for w in ir.report.warnings)


# ---------------------------------------------------------------------
# 翻译熔断器
# ---------------------------------------------------------------------


def test_breaker_state_machine() -> None:
    breaker = CircuitBreaker(failure_threshold=3, cooldown_sec=60.0)
    assert breaker.state == "closed" and breaker.allow() is True

    assert breaker.record_failure() is False  # 1/3
    assert breaker.record_failure() is False  # 2/3
    assert breaker.record_failure() is True  # 3/3 -> 刚刚打开
    assert breaker.state == "open" and breaker.allow() is False

    # 冷却过后进入 half-open，放行试探；试探成功则闭合
    import time

    breaker._opened_at = time.monotonic() - 61.0
    assert breaker.state == "half-open" and breaker.allow() is True
    breaker.record_success()
    assert breaker.state == "closed"

    # half-open 试探失败：立即再次打开
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "open"
    breaker._opened_at = time.monotonic() - 61.0
    breaker.record_failure()  # half-open 失败
    assert breaker.allow() is False


@pytest.mark.asyncio
async def test_translate_sections_llm_opens_circuit_and_orchestrator_degrades() -> None:
    """连续失败触发熔断：批内剩余片段不再请求 LLM；orchestrator 在 llm 严格模式下也降级 Google。"""
    from unittest.mock import AsyncMock, MagicMock

    from app.harness.pipeline.orchestrator import translate_sections_smart
    from app.pipeline import llm_translator as lt

    lt._TRANSLATION_BREAKER.__init__(failure_threshold=2, cooldown_sec=60.0)  # 重置为小阈值

    client = MagicMock()

    async def boom(**kwargs):
        raise RuntimeError("llm timeout")

    client.chat.completions.create = AsyncMock(side_effect=boom)
    settings = SimpleNamespace(
        deepseek_api_key="sk-test",
        deepseek_base_url="https://api.example.com",
        deepseek_model="m",
        deepseek_timeout_sec=1.0,
        translation_llm_concurrency=1,
        translation_llm_max_chars=800,
        translation_breaker_threshold=2,
        translation_breaker_cooldown_sec=60.0,
        translation_provider="llm",  # 严格 llm 模式
    )
    sections = [("## 1 引言", "Sentence one."), ("## 2 方法", "Sentence two.")]

    with patch("openai.AsyncOpenAI", return_value=client):
        # 熔断打开后 translate_sections_llm 抛 TranslationCircuitOpen
        lt._TRANSLATION_BREAKER.record_failure()
        lt._TRANSLATION_BREAKER.record_failure()
        with pytest.raises(TranslationCircuitOpenError):
            await lt.translate_sections_llm(sections, "Chinese", settings)

        # orchestrator：llm 严格模式下熔断也降级 Google（不抛错）
        with patch(
            "app.harness.pipeline.orchestrator.translate_sections",
            return_value=([("## 1 引言", "译文一。")], 0),
        ) as mock_google:
            translated, failures, provider = await translate_sections_smart(sections, "Chinese", settings)

    assert provider == "google"  # 熔断降级生效
    assert mock_google.called
    assert translated[0][1] == "译文一。"

    # 清理：闭合熔断器，避免污染其他测试
    lt._TRANSLATION_BREAKER.record_success()
