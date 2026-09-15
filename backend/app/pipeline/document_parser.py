# PDF解析、章节切分、标题识别、结构感知分块
#
# 兼容入口：旧函数（extract_text_from_pdf / chunk_text / split_text_into_sections）保留，
# 新代码请使用 parse_pdf_document()（ParserRouter -> DocumentIR）与 chunk_sections()。
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .common_utils import remove_surrogates
from .document_ir import DocumentIR
from .section_headings import (
    CN_HEADINGS,
    EN_TO_CN_SECTION_MAP,
    PAGE_MARK_RE,
    normalize_heading_line,
    normalize_heading_name,
    parse_section_heading,
    split_text_into_sections,
)

# 独立公式块（$$...$$）
FORMULA_BLOCK_RE = re.compile(r"\$\$.+?\$\$", re.DOTALL)
# 句子边界（用于超长文本段的二次切分）
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？.!?])\s+")


# ---------------------------------------------------------------------
# PDF 解析（新通道：ParserRouter -> DocumentIR）
# ---------------------------------------------------------------------


def parse_document(file_path: Path, settings: Any | None = None) -> DocumentIR:
    """
    【文档结构化解析（统一入口，FileRouter）】
    按扩展名分发：PDF -> ParserRouter（PyMuPDF 主通道 / pypdf 兜底 / MinerU / OCR）；
    Markdown / DOCX -> 专用后端。产出统一的 DocumentIR。

    失败语义：ok=False + error_kind（scanned_pdf / encrypted_pdf / corrupt_pdf / empty_text），
    调用方必须检查 ok，不允许把 error 文案当正文继续流转。
    """
    from .parser_backend import parse_any_document

    return parse_any_document(Path(file_path), settings)


def parse_pdf_document(pdf_path: Path, settings: Any | None = None) -> DocumentIR:
    """【PDF 解析（兼容入口）】仅处理 PDF；新代码使用 parse_document()。"""
    from .parser_backend import ParserRouter

    return ParserRouter(settings).parse(Path(pdf_path))


# ---------------------------------------------------------------------
# 结构感知分块（Structure-aware Chunking）
# ---------------------------------------------------------------------


def _flush_buffer(buffer: list[str], element_type: str, units: list[tuple[str, str]]) -> list[str]:
    """把缓冲行输出为原子单元；返回空缓冲（避免闭包捕获循环变量）。"""
    if buffer:
        units.append(("\n".join(buffer).strip(), element_type))
    return []


def split_content_units(content: str) -> list[tuple[str, str]]:
    """把章节内容拆成原子单元 [(text, element_type)]。

    - $$...$$ 公式块为原子单元（formula）；
    - 以 | 开头的连续行视为 Markdown 表格（table）；
    - 其余按空行分段（text）。
    """
    units: list[tuple[str, str]] = []
    for part in re.split(r"(\$\$.+?\$\$)", content, flags=re.DOTALL):
        if not part.strip():
            continue
        if part.startswith("$$"):
            units.append((part.strip(), "formula"))
            continue

        text_buffer: list[str] = []
        table_buffer: list[str] = []
        for line in part.splitlines():
            stripped = line.strip()
            if stripped.startswith("|"):
                text_buffer = _flush_buffer(text_buffer, "text", units)
                table_buffer.append(stripped)
            elif not stripped:
                if table_buffer:
                    table_buffer = _flush_buffer(table_buffer, "table", units)
                else:
                    text_buffer = _flush_buffer(text_buffer, "text", units)
            else:
                table_buffer = _flush_buffer(table_buffer, "table", units)
                text_buffer.append(stripped)
        table_buffer = _flush_buffer(table_buffer, "table", units)
        text_buffer = _flush_buffer(text_buffer, "text", units)
    return units


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """超长纯文本段按句子边界切分（兜底，正常段落很少触发）。"""
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= chunk_size:
        return [normalized] if normalized else []

    sentences = [s for s in SENTENCE_SPLIT_RE.split(normalized) if s.strip()]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > chunk_size:
            if current:
                pieces.append(current)
                current = ""
            for start in range(0, len(sentence), chunk_size - overlap):
                pieces.append(sentence[start : start + chunk_size])
            continue
        if current and len(current) + 1 + len(sentence) > chunk_size:
            pieces.append(current)
            keep = current[-overlap:] if overlap > 0 else ""
            current = (keep + " " + sentence).strip() if keep else sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _split_oversized_table(table: str, chunk_size: int) -> list[str]:
    """超大表格按行组切分，每个子块重复表头（| header | + | --- |）。"""
    lines = table.splitlines()
    if len(lines) <= 2:
        return [table]
    header = lines[:2]
    body = lines[2:]
    header_text = "\n".join(header)
    pieces: list[str] = []
    current_rows: list[str] = []
    current_len = len(header_text)

    for row in body:
        if current_len + len(row) + 1 > chunk_size and current_rows:
            pieces.append("\n".join(header + current_rows))
            current_rows = []
            current_len = len(header_text)
        current_rows.append(row)
        current_len += len(row) + 1
    if current_rows:
        pieces.append("\n".join(header + current_rows))
    return pieces


def chunk_sections_with_meta(
    sections: list[tuple[str, str]],
    chunk_size: int,
    overlap: int,
    section_pages: dict[str, int] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """
    【结构感知分块（带元数据）】
    按章节切块；公式块 / 表格为原子单元不切断；超大表格按行组切分并重复表头；
    每块携带 element_type / section / page / is_reference 元数据供向量库与检索过滤使用。

    参数:
        section_pages: 章节标题 -> 起始页码 映射（来自 DocumentIR 标题节点），
                       用于把块定位回原始页；缺省时 page 为 0。

    返回:
        (chunks, metas)，两者按索引一一对应。
    """
    from .section_headings import is_references_section

    chunks: list[str] = []
    metas: list[dict[str, Any]] = []
    pages = section_pages or {}

    for heading, content in sections:
        is_ref = is_references_section(heading)
        section_label = heading.lstrip("# ").strip() or heading
        section_page = int(pages.get(heading, pages.get(section_label, 0)) or 0)
        for unit_text, unit_type in split_content_units(content):
            if not unit_text.strip():
                continue
            element_type = "reference" if is_ref else unit_type

            if len(unit_text) <= chunk_size:
                pieces = [unit_text]
            elif unit_type == "formula":
                pieces = [unit_text]  # 公式永远原子，宁可超长不切断
            elif unit_type == "table":
                pieces = _split_oversized_table(unit_text, chunk_size)
            else:
                pieces = _split_long_text(unit_text, chunk_size, overlap)

            for piece in pieces:
                if not piece.strip():
                    continue
                chunks.append(f"{heading}\n{piece}" if heading.startswith("#") else piece)
                metas.append(
                    {
                        "element_type": element_type,
                        "section": section_label,
                        "page": section_page,
                        "is_reference": is_ref,
                    }
                )
    return chunks, metas


def chunk_sections(sections: list[tuple[str, str]], chunk_size: int, overlap: int) -> list[str]:
    """【结构感知分块】chunk_sections_with_meta 的纯文本版本。"""
    return chunk_sections_with_meta(sections, chunk_size, overlap)[0]


# ---------------------------------------------------------------------
# 旧接口（向后兼容，勿在新代码中使用）
# ---------------------------------------------------------------------


def extract_text_from_pdf(pdf_path: Path) -> str:
    """
    【PDF 文本提取（旧接口）】
    pypdf 纯文本抽取，仅为兼容保留；新代码使用 parse_pdf_document()。

    返回:
        提取的完整文本（含页码标记）；无文本时返回错误提示字符串。
    """
    try:
        reader = PdfReader(str(pdf_path))
        pages: list[str] = []
        for idx, page in enumerate(reader.pages):
            text = remove_surrogates((page.extract_text() or "").strip())
            if not text:
                continue
            pages.append(f"[Page {idx + 1}]\n{text}")
        combined = remove_surrogates("\n\n".join(pages).strip())
        if combined:
            return combined
    except Exception:
        pass
    return "未提取到可读文本，上传的 PDF 可能是扫描件或受保护文档。"


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    【文本分块（旧接口）】固定窗口 + 重叠，会破坏公式/表格结构；
    新代码使用 chunk_sections()（章节感知、公式表格原子）。
    """
    normalized = re.sub(r"\s+", " ", remove_surrogates(text)).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start = max(0, end - overlap)
    return chunks


__all__ = [
    "CN_HEADINGS",
    "EN_TO_CN_SECTION_MAP",
    "PAGE_MARK_RE",
    "chunk_sections",
    "chunk_sections_with_meta",
    "chunk_text",
    "extract_text_from_pdf",
    "normalize_heading_line",
    "normalize_heading_name",
    "parse_document",
    "parse_pdf_document",
    "parse_section_heading",
    "split_content_units",
    "split_text_into_sections",
]
