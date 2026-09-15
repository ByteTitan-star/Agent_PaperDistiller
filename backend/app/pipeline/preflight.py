"""PDF Preflight 预检查：快速判定文档类型并给出解析路由建议。

原则（见 todo.md）：有文本层的 PDF 绝不全文 OCR —— OCR 只用于"无可靠文字层"的扫描件。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .document_ir import PreflightReport

logger = logging.getLogger(__name__)

# 单页有效字符数低于该阈值视为"无文本页"（正文页通常数百字符起）
DEFAULT_MIN_CHARS_PER_PAGE = 24
# 无文本页占比超过该阈值判定为扫描件
DEFAULT_SCANNED_RATIO_THRESHOLD = 0.6


def _pymupdf_available() -> bool:
    try:
        import pymupdf  # noqa: F401
    except Exception:
        try:
            import fitz  # noqa: F401  # 旧版包名兼容
        except Exception:
            return False
    return True


def _open_with_pymupdf(pdf_path: Path):
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore[no-redef]
    return pymupdf.open(str(pdf_path))


def preflight_pdf(
    pdf_path: Path,
    *,
    min_chars_per_page: int = DEFAULT_MIN_CHARS_PER_PAGE,
    scanned_ratio_threshold: float = DEFAULT_SCANNED_RATIO_THRESHOLD,
) -> PreflightReport:
    """检查 PDF：页数 / 文本层 / 扫描占比 / 加密 / 双栏猜测，输出 parser_route。

    parser_route 语义：
    - "native"  有文本层 → 直接抽取（PyMuPDF 主通道）
    - "scanned" 无文本层 → 需要 OCR / MinerU 通道
    - "fallback" 打不开或加密 → 由路由器逐个兜底，最终大概率失败并明确报错
    """
    report = PreflightReport()

    if _pymupdf_available():
        try:
            _preflight_with_pymupdf(pdf_path, report, min_chars_per_page)
        except Exception as exc:
            report.warnings.append(f"pymupdf preflight 失败: {exc}")
            _preflight_with_pypdf(pdf_path, report, min_chars_per_page)
    else:
        _preflight_with_pypdf(pdf_path, report, min_chars_per_page)

    _decide_route(report, scanned_ratio_threshold)
    return report


def _preflight_with_pymupdf(pdf_path: Path, report: PreflightReport, min_chars_per_page: int) -> None:
    doc = _open_with_pymupdf(pdf_path)
    if doc.needs_pass:
        report.encrypted = True
        report.warnings.append("PDF 已加密（needs_pass）")
        doc.close()
        return

    report.page_count = doc.page_count
    text_pages = 0
    two_column_pages = 0
    total_chars = 0

    for page in doc:
        text = page.get_text("text") or ""
        page_chars = len(text.strip())
        total_chars += page_chars
        if page_chars >= min_chars_per_page:
            text_pages += 1
        if _looks_two_column(page):
            two_column_pages += 1
    doc.close()

    report.char_count = total_chars
    empty_pages = max(0, report.page_count - text_pages)
    report.scanned_ratio = (empty_pages / report.page_count) if report.page_count else 0.0
    report.has_text_layer = text_pages > 0 and report.scanned_ratio < 0.999
    if report.page_count and two_column_pages >= max(1, report.page_count // 2):
        report.suspected_two_column = True


def _looks_two_column(page) -> bool:
    """猜测单页是否双栏：文本块中心点集中在页面左右两个纵向带内。"""
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and (b[4] or "").strip()]
    if len(blocks) < 6:
        return False
    width = page.rect.width
    if width <= 0:
        return False
    mid = width / 2
    left = [b for b in blocks if (b[0] + b[2]) / 2 < mid]
    right = [b for b in blocks if (b[0] + b[2]) / 2 >= mid]
    # 双栏：两侧各有足量块，且跨中线的块（宽块）很少
    wide = [b for b in blocks if (b[2] - b[0]) > width * 0.7]
    return len(left) >= 3 and len(right) >= 3 and len(wide) <= len(blocks) // 3


def _preflight_with_pypdf(pdf_path: Path, report: PreflightReport, min_chars_per_page: int) -> None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                report.encrypted = True
                report.warnings.append("PDF 加密且空口令解密失败")
                return
        report.page_count = len(reader.pages)
        text_pages = 0
        total_chars = 0
        for page in reader.pages:
            page_chars = len((page.extract_text() or "").strip())
            total_chars += page_chars
            if page_chars >= min_chars_per_page:
                text_pages += 1
        report.char_count = total_chars
        empty_pages = max(0, report.page_count - text_pages)
        report.scanned_ratio = (empty_pages / report.page_count) if report.page_count else 0.0
        report.has_text_layer = text_pages > 0 and report.scanned_ratio < 0.999
    except Exception as exc:
        report.warnings.append(f"pypdf preflight 失败: {exc}")


def _decide_route(report: PreflightReport, scanned_ratio_threshold: float) -> None:
    if report.encrypted:
        report.parser_route = "fallback"
        return
    if report.page_count == 0:
        report.parser_route = "fallback"
        report.warnings.append("未读取到任何页面，文件可能损坏")
        return
    if not report.has_text_layer or report.scanned_ratio >= scanned_ratio_threshold:
        report.parser_route = "scanned"
        report.warnings.append(f"疑似扫描件（无文本页占比 {report.scanned_ratio:.0%}），需要 OCR 通道")
        return
    report.parser_route = "native"


__all__ = [
    "DEFAULT_MIN_CHARS_PER_PAGE",
    "DEFAULT_SCANNED_RATIO_THRESHOLD",
    "preflight_pdf",
]
