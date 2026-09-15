"""解析后端与自动路由（ParserRouter）。

分层（见 todo.md）：
- ParserBackend 协议：统一 parse() -> DocumentIR；
- PyMuPDFBackend：原生 PDF 主通道（双栏阅读顺序 / 字号标题 / 表格 Markdown / 断词修复）；
- PypdfBackend：零依赖兜底（原 pypdf 行为）；
- MinerUBackend：复杂论文 PDF（版面模型 + 公式 LaTeX + 表格 HTML），CLI 守卫、默认关闭；
- PaddleOCRBackend：扫描件 OCR，守卫导入、默认关闭；
- ParserRouter：Preflight 判定 → 引擎链逐个尝试 → Quality Gate 不达标自动降级。

重要：任何后端失败都不返回"错误文案当正文"，而是 ok=False + error_kind，
由 Router 决定降级或最终失败（错误传播）。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .document_ir import (
    ERROR_CORRUPT,
    ERROR_EMPTY,
    ERROR_ENCRYPTED,
    ERROR_SCANNED,
    DocNode,
    DocumentIR,
    PreflightReport,
    failed_ir,
)
from .document_parser import split_content_units
from .layout_detector import formula_regions
from .preflight import DEFAULT_MIN_CHARS_PER_PAGE, preflight_pdf
from .section_headings import (
    CAPTION_RE,
    is_references_section,
    parse_section_heading,
    split_text_into_sections,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# 配置读取（兼容 Settings 与测试用 SimpleNamespace）
# ---------------------------------------------------------------------


def _cfg(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None)
    return default if value is None else value


@dataclass
class LineInfo:
    """单个文本行及其版面信息（PyMuPDF 通道内部结构）。"""

    text: str
    size: float
    bold: bool
    page: int
    bbox: tuple[float, float, float, float]
    is_math: bool = False  # 数学字形密集行（公式区域候选）


# 数学 Unicode 区块 + Latin-1 数学符号（原生 PDF 公式的字形特征）
MATH_CHAR_RE = re.compile(
    "[\u2200-\u22ff\u2190-\u21ff\u2a00-\u2aff\u00b1\u00d7\u00f7\u00b9\u00ba\u00bb\u00bc\u00bd\u00be±≤≥≠∑∫√∞∈∀∃→⇒]"
)
# TeX/数学字体名特征（CMMI/CMSY/CMEX 等）
MATH_FONT_HINTS = ("cmmi", "cmsy", "cmex", "math", "symbol", "mtmi", "mtsy")
# 行内数学字形占比达到该阈值视为公式行
FORMULA_LINE_RATIO = 0.3
FORMULA_LINE_MIN_CHARS = 4


def math_glyph_ratio(text: str) -> float:
    """数学字形字符占比（公式行检测的启发式特征）。"""
    stripped = text.strip()
    if not stripped:
        return 0.0
    return len(MATH_CHAR_RE.findall(stripped)) / len(stripped)


def _span_is_math(span: dict) -> bool:
    font = str(span.get("font", "")).lower()
    return any(hint in font for hint in MATH_FONT_HINTS)


def is_formula_text(text: str) -> bool:
    """纯文本启发式：数学字形密集（公式区域候选行）。"""
    stripped = text.strip()
    return len(stripped) >= FORMULA_LINE_MIN_CHARS and math_glyph_ratio(stripped) >= FORMULA_LINE_RATIO


def _pymupdf():
    try:
        import pymupdf

        return pymupdf
    except ImportError:
        import fitz as pymupdf  # type: ignore[no-redef]

        return pymupdf


def _pymupdf_available() -> bool:
    try:
        _pymupdf()
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------
# 协议
# ---------------------------------------------------------------------


class ParserBackend(Protocol):
    """解析后端协议：产出 DocumentIR，失败抛异常或返回 ok=False。"""

    name: str

    def is_available(self) -> bool: ...

    def parse(self, pdf_path: Path, *, report: PreflightReport | None = None) -> DocumentIR: ...


# ---------------------------------------------------------------------
# PyMuPDF 主通道
# ---------------------------------------------------------------------


class PyMuPDFBackend:
    """原生 PDF 结构化解析（替代 pypdf 纯文本抽取）。

    能力：
    - 块级阅读顺序：双栏页先左栏后右栏，单栏页自上而下；
    - 标题识别双通道：字号启发（正文字号聚类）+ 正则词表（section_headings）；
    - 表格：find_tables() 转 Markdown，并从正文块中剔除表格区域避免重复；
    - 断词连字符修复（"soft-\\nware" -> "software"）；
    - 图片 / 表格节点登记（含题注绑定）；
    - 公式链路（可选，formula_backend != off 时启用）：数学字形行检测 ->
      页面区域裁剪 -> FormulaRecognizer 转 LaTeX -> $$...$$ 回填正文与 equation 节点，
      识别失败保留原始字形文本（优雅降级）。
    """

    name = "pymupdf"

    def __init__(
        self,
        formula_recognizer: Any | None = None,
        layout_detector: Any | None = None,
    ) -> None:
        self._formula_recognizer = formula_recognizer
        self._layout_detector = layout_detector

    def is_available(self) -> bool:
        return _pymupdf_available()

    def parse(self, pdf_path: Path, *, report: PreflightReport | None = None) -> DocumentIR:
        pymupdf = _pymupdf()
        try:
            doc = pymupdf.open(str(pdf_path))
        except Exception as exc:
            return failed_ir(ERROR_CORRUPT, f"无法打开 PDF: {exc}", parser=self.name, report=report)

        if doc.needs_pass:
            authenticated = doc.authenticate("")
            if not authenticated:
                doc.close()
                return failed_ir(ERROR_ENCRYPTED, "PDF 已加密，无法解析", parser=self.name, report=report)

        pf = report or preflight_pdf(pdf_path, min_chars_per_page=DEFAULT_MIN_CHARS_PER_PAGE)
        try:
            body_size = self._detect_body_size(doc)
            (
                ordered_lines,
                table_nodes,
                figure_nodes,
                caption_lines,
                equation_nodes,
                image_formulas,
            ) = self._extract_pages(doc, pf)
        finally:
            doc.close()

        sections, heading_nodes, references = self._group_sections(ordered_lines, body_size)
        full_text = self._build_full_text(ordered_lines, extra_page_blocks=image_formulas)

        nodes: list[DocNode] = list(heading_nodes)
        nodes.extend(equation_nodes)
        nodes.extend(self._paragraph_nodes(ordered_lines, heading_nodes))
        nodes.extend(self._bind_captions(table_nodes + figure_nodes, caption_lines))
        nodes.extend(references)

        ir = DocumentIR(
            parser=self.name,
            text=full_text,
            sections=sections,
            nodes=nodes,
            report=pf,
        )
        if not ir.effective_chars:
            return failed_ir(
                ERROR_SCANNED if pf.parser_route == "scanned" else ERROR_EMPTY,
                "PyMuPDF 未提取到有效文本（疑似扫描件或纯图片 PDF）",
                parser=self.name,
                report=pf,
            )
        return ir

    # ---------------- 内部实现 ----------------

    def _detect_body_size(self, doc) -> float:
        """正文字号 = 前 12 页 span 字号的字符数加权众数。"""
        weights: dict[float, int] = {}
        for page in doc:
            if page.number >= 12:
                break
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        size = round(float(span.get("size", 0.0)), 1)
                        text = span.get("text", "")
                        if size <= 0 or not text.strip():
                            continue
                        weights[size] = weights.get(size, 0) + len(text.strip())
        if not weights:
            return 10.0
        return max(weights.items(), key=lambda kv: kv[1])[0]

    def _extract_pages(
        self, doc, pf: PreflightReport
    ) -> tuple[list[LineInfo], list[DocNode], list[DocNode], list[LineInfo], list[DocNode], list[tuple[int, str]]]:
        ordered_lines: list[LineInfo] = []
        table_nodes: list[DocNode] = []
        figure_nodes: list[DocNode] = []
        caption_lines: list[LineInfo] = []
        equation_nodes: list[DocNode] = []
        image_formulas: list[tuple[int, str]] = []  # (page_no, $$LaTeX$$)——图片型公式回填全文

        table_seq = 0
        figure_seq = 0
        for page in doc:
            page_no = page.number + 1
            width = page.rect.width
            height = page.rect.height
            if width <= 0 or height <= 0:
                continue

            table_bboxes: list[tuple[float, float, float, float]] = []
            try:
                finder = page.find_tables()
                for tbl in getattr(finder, "tables", []) or []:
                    rows = tbl.extract()
                    markdown = _rows_to_markdown(rows)
                    if not markdown:
                        continue
                    table_seq += 1
                    x0, y0, x1, y1 = tbl.bbox
                    table_bboxes.append(tbl.bbox)
                    table_nodes.append(
                        DocNode(
                            node_id=f"table-p{page_no}-{table_seq}",
                            type="table",
                            text=markdown,
                            page=page_no,
                            bbox=[x0, y0, x1, y1],
                            meta={"seq": table_seq},
                        )
                    )
            except Exception as exc:  # 表格识别失败不影响正文抽取
                pf.warnings.append(f"第 {page_no} 页表格识别失败: {exc}")

            raw_blocks = [b for b in page.get_text("dict")["blocks"] if b.get("type") == 0]
            page_lines: list[LineInfo] = []
            for block in raw_blocks:
                bx0, by0, bx1, by1 = block["bbox"]
                if _center_inside(bx0, bx1, by0, by1, table_bboxes):
                    continue  # 表格区域内的文本块已由表格通道产出
                for line in block.get("lines", []):
                    line_text = "".join(span.get("text", "") for span in line.get("spans", []))
                    if not line_text.strip():
                        continue
                    spans_with_text = [span for span in line.get("spans", []) if span.get("text", "").strip()]
                    sizes = [float(span.get("size", 0.0)) for span in spans_with_text]
                    line_size = max(sizes) if sizes else 0.0
                    line_bold = any("bold" in str(span.get("font", "")).lower() for span in spans_with_text)
                    line_math = any(_span_is_math(span) for span in spans_with_text) or is_formula_text(line_text)
                    lx0, ly0, lx1, ly1 = line["bbox"]
                    page_lines.append(
                        LineInfo(
                            text=line_text.strip(),
                            size=line_size,
                            bold=line_bold,
                            page=page_no,
                            bbox=(lx0, ly0, lx1, ly1),
                            is_math=line_math,
                        )
                    )

            # 图片块登记（type==1）
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 1:
                    continue
                bx0, by0, bx1, by1 = block["bbox"]
                if (bx1 - bx0) < 40 or (by1 - by0) < 40:
                    continue  # 过滤装饰性小图
                figure_seq += 1
                figure_nodes.append(
                    DocNode(
                        node_id=f"figure-p{page_no}-{figure_seq}",
                        type="figure",
                        text="",
                        page=page_no,
                        bbox=[bx0, by0, bx1, by1],
                        meta={"seq": figure_seq},
                    )
                )

            # 阅读顺序：双栏先左后右，单栏自上而下
            mid = width / 2
            has_two_cols = (
                len(page_lines) >= 6
                and sum(1 for ln in page_lines if (ln.bbox[0] + ln.bbox[2]) / 2 < mid) >= 3
                and sum(1 for ln in page_lines if (ln.bbox[0] + ln.bbox[2]) / 2 >= mid) >= 3
            )
            if has_two_cols:
                left = [ln for ln in page_lines if (ln.bbox[0] + ln.bbox[2]) / 2 < mid]
                right = [ln for ln in page_lines if (ln.bbox[0] + ln.bbox[2]) / 2 >= mid]
                ordered = sorted(left, key=lambda ln: (ln.bbox[1], ln.bbox[0])) + sorted(
                    right, key=lambda ln: (ln.bbox[1], ln.bbox[0])
                )
            else:
                ordered = sorted(page_lines, key=lambda ln: (ln.bbox[1], ln.bbox[0]))

            for ln in ordered:
                stripped = ln.text.strip()
                if not stripped or re.fullmatch(r"(Page\s+)?\d{1,4}( of \d+)?", stripped, re.IGNORECASE):
                    continue  # 页码行
                if CAPTION_RE.match(stripped):
                    caption_lines.append(ln)
                ordered_lines.append(ln)

            # 公式链路：版面模型检测公式区域（优先）-> 行标记 -> 裁剪识别 -> $$LaTeX$$ 回填
            if self._layout_detector is not None:
                unmatched = self._mark_formula_lines_with_detector(page, ordered_lines, pf)
                # 图片型公式（区域内无文本行）：直接裁剪识别，产出 equation 节点
                self._recognize_image_formulas(page, unmatched, equation_nodes, image_formulas)
            self._apply_formula_recognition(page, ordered_lines, equation_nodes)

        return ordered_lines, table_nodes, figure_nodes, caption_lines, equation_nodes, image_formulas

    def _mark_formula_lines_with_detector(
        self, page, ordered_lines: list[LineInfo], pf: PreflightReport
    ) -> list[tuple[float, float, float, float]]:
        """版面模型检测 display 公式区域 -> 区域内文本行标记为公式行（替代字形启发式）。

        返回未命中任何文本行的区域（PDF pt 坐标）——图片型公式，交由
        _recognize_image_formulas 直接裁剪识别。检测失败只告警回退启发式。
        """
        try:
            import numpy as np

            zoom = 2.0
            pixmap = page.get_pixmap(matrix=_pymupdf().Matrix(zoom, zoom), alpha=False)
            rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
            assert self._layout_detector is not None  # 调用方已判空，仅为类型收窄
            regions = formula_regions(self._layout_detector.detect(rgb))
            if not regions:
                return []
            # 像素坐标 -> PDF pt 坐标
            boxes = [(d.bbox[0] / zoom, d.bbox[1] / zoom, d.bbox[2] / zoom, d.bbox[3] / zoom) for d in regions]
            page_no = page.number + 1
            matched_boxes: set[int] = set()
            for line in ordered_lines:
                if line.page != page_no or line.is_math:
                    continue
                center_x = (line.bbox[0] + line.bbox[2]) / 2
                center_y = (line.bbox[1] + line.bbox[3]) / 2
                for box_idx, (bx0, by0, bx1, by1) in enumerate(boxes):
                    if bx0 <= center_x <= bx1 and by0 <= center_y <= by1:
                        line.is_math = True
                        matched_boxes.add(box_idx)
                        break
            if matched_boxes:
                logger.info("[版面检测] 第 %d 页：%d 行落入公式区域（模型检测）", page_no, len(matched_boxes))
            return [boxes[i] for i in range(len(boxes)) if i not in matched_boxes]
        except Exception as exc:
            pf.warnings.append(f"第 {page.number + 1} 页版面检测失败（回退字形启发式）: {exc}")
            logger.warning("[版面检测] 第 %d 页检测异常: %s", page.number + 1, exc)
        return []

    def _recognize_image_formulas(
        self,
        page,
        regions: list[tuple[float, float, float, float]],
        equation_nodes: list[DocNode],
        image_formulas: list[tuple[int, str]],
    ) -> None:
        """图片型公式区域（无文本行）：裁剪 -> 识别 -> equation 节点 + 全文回填。"""
        if not regions or self._formula_recognizer is None:
            return
        page_no = page.number + 1
        for x0, y0, x1, y1 in regions:
            latex = self._recognize_clip(page, x0, y0, x1, y1)
            if not latex:
                continue
            display = f"$${latex}$$"
            equation_nodes.append(
                DocNode(
                    node_id=f"eq-img-p{page_no}-{len(equation_nodes) + 1}",
                    type="equation",
                    text=display,
                    latex=latex,
                    page=page_no,
                    bbox=[x0, y0, x1, y1],
                    meta={"source": "image_formula"},
                )
            )
            image_formulas.append((page_no, display))

    def _recognize_clip(self, page, x0: float, y0: float, x1: float, y1: float) -> str | None:
        """裁剪页面区域渲染 PNG 并识别为 LaTeX（失败返回 None）。"""
        try:
            pymupdf = _pymupdf()
            clip = pymupdf.Rect(max(0, x0), max(0, y0), min(page.rect.x1, x1), min(page.rect.y1, y1))
            if clip.is_empty or clip.width < 2 or clip.height < 2:
                return None
            pixmap = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(3, 3))
            latex = self._formula_recognizer.recognize(pixmap.tobytes("png"))  # type: ignore[union-attr]
            if not latex or len(str(latex)) > 2000:
                return None
            return str(latex).strip()
        except Exception as exc:
            logger.warning("[公式识别] 图片型公式区域识别失败: %s", exc)
            return None

    def _apply_formula_recognition(self, page, ordered_lines: list[LineInfo], equation_nodes: list[DocNode]) -> None:
        """对当前页的连续公式行区域做裁剪识别；失败保留原始字形文本。

        只在注入了 FormulaRecognizer（formula_backend != off）时生效，
        替换后的 $$...$$ 行会被下游切块当作原子公式单元。
        """
        if self._formula_recognizer is None:
            return
        idx = 0
        while idx < len(ordered_lines):
            line = ordered_lines[idx]
            if not line.is_math or line.page != page.number + 1:
                idx += 1
                continue
            end = idx
            while end < len(ordered_lines):
                candidate = ordered_lines[end]
                if candidate.page != page.number + 1 or not candidate.is_math:
                    break
                end += 1
            region = ordered_lines[idx:end]
            latex = self._recognize_region(page, region)
            if latex:
                display = f"$${latex}$$"
                x0 = min(ln.bbox[0] for ln in region)
                y0 = min(ln.bbox[1] for ln in region)
                x1 = max(ln.bbox[2] for ln in region)
                y1 = max(ln.bbox[3] for ln in region)
                region[0].text = display
                region[0].is_math = False
                for stale in region[1:]:
                    stale.text = ""
                    stale.is_math = False
                equation_nodes.append(
                    DocNode(
                        node_id=f"eq-p{page.number + 1}-{len(equation_nodes) + 1}",
                        type="equation",
                        text=display,
                        latex=latex,
                        page=page.number + 1,
                        bbox=[x0, y0, x1, y1],
                    )
                )
            idx = end

    def _recognize_region(self, page, region: list[LineInfo]) -> str | None:
        """裁剪公式区域渲染为 PNG 并识别为 LaTeX；任何失败返回 None。"""
        if not region:
            return None
        try:
            pymupdf = _pymupdf()
            x0 = min(ln.bbox[0] for ln in region) - 2
            y0 = min(ln.bbox[1] for ln in region) - 2
            x1 = max(ln.bbox[2] for ln in region) + 2
            y1 = max(ln.bbox[3] for ln in region) + 2
            clip = pymupdf.Rect(max(0, x0), max(0, y0), min(page.rect.x1, x1), min(page.rect.y1, y1))
            if clip.is_empty or clip.width < 2 or clip.height < 2:
                return None
            pixmap = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(2, 2))
            png_bytes = pixmap.tobytes("png")
            latex = self._formula_recognizer.recognize(png_bytes)  # type: ignore[union-attr]
            if not latex or not str(latex).strip() or len(str(latex)) > 2000:
                return None
            return str(latex).strip()
        except Exception as exc:
            logger.warning("[公式识别] 区域识别失败（保留原始字形）: %s", exc)
            return None

    def _group_sections(
        self, ordered_lines: list[LineInfo], body_size: float
    ) -> tuple[list[tuple[str, str]], list[DocNode], list[DocNode]]:
        """按标题（字号 + 正则双通道）把行分组成章节，并产出标题/参考文献节点。"""
        sections: list[tuple[str, str]] = []
        heading_nodes: list[DocNode] = []
        references: list[DocNode] = []

        current_title = "## 全文导读"
        current_lines: list[LineInfo] = []
        in_references = False
        ref_seq = 0

        def flush() -> None:
            nonlocal current_lines
            content = _join_block_lines(current_lines)
            if content:
                sections.append((current_title, content))
            current_lines = []

        for ln in ordered_lines:
            if not ln.text.strip():
                continue  # 公式回填后置空的行
            heading_mark: str | None = None
            if not ln.text.startswith("$$"):  # 公式行绝不参与标题识别
                regex_hit = parse_section_heading(ln.text)
                if regex_hit:
                    heading_mark, _remainder = regex_hit
                elif self._looks_like_heading(ln, body_size):
                    level = 2 if ln.size >= body_size * 1.45 else 3
                    heading_mark = f"{'#' * level} {ln.text}"

            if heading_mark:
                flush()
                current_title = heading_mark
                heading_nodes.append(
                    DocNode(
                        node_id=f"heading-{len(heading_nodes) + 1:04d}",
                        type="heading",
                        text=heading_mark,
                        page=ln.page,
                        level=heading_mark.count("#"),
                        section_path=[heading_mark.lstrip("# ").strip()],
                        bbox=list(ln.bbox),
                    )
                )
                in_references = is_references_section(heading_mark)
                continue

            if in_references and ln.text.strip():
                ref_seq += 1
                references.append(
                    DocNode(
                        node_id=f"ref-{ref_seq:04d}",
                        type="reference",
                        text=ln.text.strip(),
                        page=ln.page,
                        section_path=["参考文献"],
                    )
                )
            current_lines.append(ln)

        flush()

        # 短片段合并：与旧版不同，合并时保留其标题（折叠进正文，不丢失结构信息）
        merged: list[tuple[str, str]] = []
        for title, content in sections:
            compact = re.sub(r"\s+", " ", content).strip()
            if not compact:
                continue
            if merged and title == merged[-1][0]:
                merged[-1] = (merged[-1][0], f"{merged[-1][1]}\n{content}".strip())
                continue
            if len(compact) < 80 and merged:
                merged[-1] = (merged[-1][0], f"{merged[-1][1]}\n\n{title}\n{content}".strip())
                continue
            merged.append((title, content))

        return merged or [("## 全文", _join_block_lines(ordered_lines))], heading_nodes, references

    def _looks_like_heading(self, ln: LineInfo, body_size: float) -> bool:
        """字号启发：明显大于正文字号的短行，或短的全大写加粗行；不以句号结尾。"""
        text = ln.text.strip()
        if not text or len(text) > 90:
            return False
        if text.endswith((".", ";", ",")):
            return False
        if body_size > 0 and ln.size >= body_size * 1.12:
            return True
        looks_capped_title = ln.bold and len(text.split()) <= 10 and text.upper() == text
        return bool(looks_capped_title and re.search(r"[A-Za-z\u4e00-\u9fff]", text))

    def _paragraph_nodes(self, ordered_lines: list[LineInfo], heading_nodes: list[DocNode]) -> list[DocNode]:
        """按页与标题把行组装成段落节点（供切块元数据 / 图表证据使用）。"""
        heading_texts = {(h.page, h.text.strip()): h.text for h in heading_nodes}
        nodes: list[DocNode] = []
        current_heading = ""
        buffer: list[LineInfo] = []

        def flush(page_hint: int) -> None:
            nonlocal buffer
            text = _join_block_lines(buffer)
            if text:
                nodes.append(
                    DocNode(
                        node_id=f"para-{len(nodes) + 1:04d}",
                        type="paragraph",
                        text=text,
                        page=page_hint,
                        section_path=[current_heading] if current_heading else [],
                    )
                )
            buffer = []

        for ln in ordered_lines:
            mark = heading_texts.get((ln.page, ln.text.strip()))
            if mark is not None:
                flush(ln.page)
                current_heading = mark.lstrip("# ").strip()
                continue
            if buffer and buffer[-1].page != ln.page:
                flush(buffer[-1].page)
            buffer.append(ln)
        if buffer:
            flush(buffer[-1].page)
        return nodes

    def _bind_captions(self, nodes: list[DocNode], caption_lines: list[LineInfo]) -> list[DocNode]:
        """把 'Table N: ...' / 'Figure N: ...' 题注绑定到同页最近的表格/图片节点。"""
        for cap in caption_lines:
            match = CAPTION_RE.match(cap.text)
            if not match:
                continue
            label = match.group(1).lower()
            want_type = "table" if label.startswith(("table", "表")) else "figure"
            candidates = [n for n in nodes if n.page == cap.page and n.type == want_type]
            if not candidates:
                continue
            cap_center = (cap.bbox[1] + cap.bbox[3]) / 2
            nearest = min(
                candidates,
                key=lambda n: abs(((n.bbox or [0, 0, 0, 0])[1] + (n.bbox or [0, 0, 0, 0])[3]) / 2 - cap_center),
            )
            nearest.caption = cap.text.strip()
        return nodes

    def _build_full_text(
        self,
        ordered_lines: list[LineInfo],
        extra_page_blocks: list[tuple[int, str]] | None = None,
    ) -> str:
        pages: dict[int, list[LineInfo]] = {}
        for ln in ordered_lines:
            pages.setdefault(ln.page, []).append(ln)
        extras: dict[int, list[str]] = {}
        for page_no, block in extra_page_blocks or []:
            extras.setdefault(page_no, []).append(block)
        all_pages = set(pages) | set(extras)
        parts: list[str] = []
        for page_no in sorted(all_pages):
            blocks = []
            body = _join_block_lines(pages.get(page_no, []))
            if body:
                blocks.append(body)
            blocks.extend(extras.get(page_no, []))
            if blocks:
                parts.append(f"[Page {page_no}]\n" + "\n\n".join(blocks))
        return "\n\n".join(parts)


def _join_block_lines(lines: list[LineInfo]) -> str:
    """行 -> 段落文本：断词修复 + 行合并（相邻行以空格连接，跳过公式回填后的空行）。"""
    if not lines:
        return ""
    raw = "\n".join(ln.text for ln in lines if ln.text.strip())
    text = re.sub(r"([A-Za-z])-\n([a-z])", r"\1\2", raw)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def _center_inside(x0: float, x1: float, y0: float, y1: float, bboxes: list[tuple[float, float, float, float]]) -> bool:
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return any(bx0 <= cx <= bx1 and by0 <= cy <= by1 for bx0, by0, bx1, by1 in bboxes)


def _rows_to_markdown(rows: list[list[Any]] | None) -> str:
    """表格行数据 -> Markdown 表格（空单元格补空串，列数对齐）。"""
    if not rows:
        return ""
    cleaned = [[("" if cell is None else str(cell).replace("\n", " ").strip()) for cell in row] for row in rows]
    cleaned = [row for row in cleaned if any(cell for cell in row)]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    padded = [row + [""] * (width - len(row)) for row in cleaned]
    header = padded[0]
    separator = ["---"] * width
    body = padded[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


# ---------------------------------------------------------------------
# pypdf 兜底通道（零新依赖）
# ---------------------------------------------------------------------


class PypdfBackend:
    """原 pypdf 纯文本抽取封装：仅在 PyMuPDF 不可用 / 失败时兜底。"""

    name = "pypdf"

    def is_available(self) -> bool:
        try:
            from pypdf import PdfReader  # noqa: F401
        except Exception:
            return False
        return True

    def parse(self, pdf_path: Path, *, report: PreflightReport | None = None) -> DocumentIR:
        from pypdf import PdfReader

        from .common_utils import remove_surrogates

        pf = report or preflight_pdf(pdf_path)
        try:
            reader = PdfReader(str(pdf_path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    return failed_ir(ERROR_ENCRYPTED, "PDF 加密且空口令解密失败", parser=self.name, report=pf)

            pages: list[str] = []
            for idx, page in enumerate(reader.pages):
                text = remove_surrogates((page.extract_text() or "").strip())
                if not text:
                    continue
                pages.append(f"[Page {idx + 1}]\n{text}")
            combined = remove_surrogates("\n\n".join(pages).strip())
        except Exception as exc:
            return failed_ir(ERROR_CORRUPT, f"pypdf 打开/解析失败: {exc}", parser=self.name, report=pf)

        if not combined:
            kind = ERROR_SCANNED if pf.parser_route == "scanned" else ERROR_EMPTY
            return failed_ir(kind, "pypdf 未提取到文本（疑似扫描件或受保护文档）", parser=self.name, report=pf)

        sections = split_text_into_sections(combined)
        nodes = [
            DocNode(node_id=f"heading-{idx + 1:04d}", type="heading", text=title, page=0, level=title.count("#"))
            for title, _ in sections
            if title.startswith("#")
        ]
        return DocumentIR(parser=self.name, text=combined, sections=sections, nodes=nodes, report=pf)


# ---------------------------------------------------------------------
# MinerU 通道（复杂论文 PDF，Phase 2）
# ---------------------------------------------------------------------


class MinerUBackend:
    """MinerU CLI 适配器：版面模型 + 公式 LaTeX + 表格 HTML。

    需要单独安装 mineru（含模型权重），并通过 parser_mineru_enabled=True 启用。
    输出目录中的 content_list.json 逐项映射为 DocNode（字段名按 MinerU 2.x，
    全部 .get() 防御式读取，版本差异不会导致崩溃）。
    """

    name = "mineru"
    TIMEOUT_SEC = 1800.0

    def __init__(self, timeout_sec: float | None = None) -> None:
        self.timeout_sec = timeout_sec or self.TIMEOUT_SEC

    def is_available(self) -> bool:
        return shutil.which("mineru") is not None

    def parse(self, pdf_path: Path, *, report: PreflightReport | None = None) -> DocumentIR:
        pf = report or preflight_pdf(pdf_path)
        if not self.is_available():
            return failed_ir(ERROR_CORRUPT, "mineru CLI 不可用", parser=self.name, report=pf)

        with tempfile.TemporaryDirectory(prefix="mineru_") as tmp:
            cmd = ["mineru", "-p", str(pdf_path), "-o", tmp]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_sec,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return failed_ir(
                    ERROR_CORRUPT, f"mineru 解析超时（>{self.timeout_sec:.0f}s）", parser=self.name, report=pf
                )
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "").strip()[-300:]
                return failed_ir(ERROR_CORRUPT, f"mineru 退出码 {proc.returncode}: {tail}", parser=self.name, report=pf)

            content_file = next(Path(tmp).rglob("content_list.json"), None)
            if content_file is None:
                return failed_ir(ERROR_CORRUPT, "mineru 输出中未找到 content_list.json", parser=self.name, report=pf)
            try:
                entries = json.loads(content_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                return failed_ir(ERROR_CORRUPT, f"content_list.json 解析失败: {exc}", parser=self.name, report=pf)
            return self._entries_to_ir(entries or [], pdf_path, pf)

    def _entries_to_ir(self, entries: list[dict], pdf_path: Path, pf: PreflightReport) -> DocumentIR:
        nodes: list[DocNode] = []
        sections: list[tuple[str, str]] = []
        page_texts: dict[int, list[str]] = {}
        current_title = "## 全文导读"
        current_parts: list[str] = []

        def flush() -> None:
            nonlocal current_parts
            content = "\n\n".join(p for p in current_parts if p.strip())
            if content:
                sections.append((current_title, content))
            current_parts = []

        for idx, entry in enumerate(entries):
            etype = str(entry.get("type", "text"))
            page = int(entry.get("page_idx", 0)) + 1
            text = str(entry.get("text", "") or "")

            if etype in {"table_caption", "image_caption"}:
                caption = text.strip()
                if nodes:
                    target = next(
                        (n for n in reversed(nodes) if n.type in {"table", "figure"} and n.caption is None), None
                    )
                    if target is not None:
                        target.caption = caption
                nodes.append(DocNode(node_id=f"caption-{idx:04d}", type="caption", text=caption, page=page))
                page_texts.setdefault(page, []).append(caption)
                continue

            if etype == "title":
                level = min(4, max(2, int(entry.get("level", 1)) + 1))
                mark = f"{'#' * level} {text.strip()}"
                flush()
                current_title = mark
                nodes.append(
                    DocNode(
                        node_id=f"heading-{idx:04d}",
                        type="heading",
                        text=mark,
                        page=page,
                        level=level,
                        section_path=[text.strip()],
                    )
                )
                page_texts.setdefault(page, []).append(text.strip())
                continue

            if etype == "equation":
                latex = str(entry.get("latex") or entry.get("text") or "").strip()
                display = f"$${latex}$$"
                nodes.append(DocNode(node_id=f"eq-{idx:04d}", type="equation", text=display, latex=latex, page=page))
                current_parts.append(display)
                page_texts.setdefault(page, []).append(display)
                continue

            if etype == "table":
                html = entry.get("html") or None
                markdown = _rows_to_markdown(entry.get("rows")) if entry.get("rows") else (html or "")
                nodes.append(DocNode(node_id=f"table-{idx:04d}", type="table", text=markdown, html=html, page=page))
                current_parts.append(markdown)
                page_texts.setdefault(page, []).append(markdown)
                continue

            if etype == "image":
                img_path = str(entry.get("img_path", "") or "")
                nodes.append(
                    DocNode(node_id=f"figure-{idx:04d}", type="figure", text="", page=page, meta={"img_path": img_path})
                )
                continue

            # 普通文本
            if text.strip():
                nodes.append(DocNode(node_id=f"para-{idx:04d}", type="paragraph", text=text.strip(), page=page))
                current_parts.append(text.strip())
                page_texts.setdefault(page, []).append(text.strip())

        flush()

        full_text = "\n\n".join(
            f"[Page {page_no}]\n" + "\n".join(parts) for page_no, parts in sorted(page_texts.items())
        )
        ir = DocumentIR(parser=self.name, text=full_text, sections=sections, nodes=nodes, report=pf)
        if not ir.effective_chars:
            return failed_ir(ERROR_EMPTY, "mineru 未产出有效文本", parser=self.name, report=pf)
        return ir


# ---------------------------------------------------------------------
# PaddleOCR 扫描件通道（Phase 2）
# ---------------------------------------------------------------------


class PaddleOCRBackend:
    """扫描件 OCR：PyMuPDF 渲染页面 -> PaddleOCR 识别 -> 按页组装。

    需要 pip install paddleocr paddlepaddle，并通过 parser_ocr_enabled=True 启用。
    """

    name = "paddleocr"

    def is_available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
        except Exception:
            return False
        return _pymupdf_available()

    def parse(self, pdf_path: Path, *, report: PreflightReport | None = None) -> DocumentIR:
        pymupdf = _pymupdf()
        try:
            from paddleocr import PaddleOCR
        except Exception as exc:
            return failed_ir(ERROR_CORRUPT, f"paddleocr 导入失败: {exc}", parser=self.name, report=report)

        pf = report or preflight_pdf(pdf_path)
        try:
            ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        except TypeError:  # 新版 paddleocr 移除了 show_log 参数
            ocr = PaddleOCR(use_angle_cls=True, lang="en")

        try:
            doc = pymupdf.open(str(pdf_path))
        except Exception as exc:
            return failed_ir(ERROR_CORRUPT, f"无法打开 PDF: {exc}", parser=self.name, report=pf)

        pages: list[str] = []
        try:
            for page in doc:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                png = pix.tobytes("png")
                result = ocr.ocr(png, cls=True)
                lines: list[str] = []
                for block in result or []:
                    for item in block or []:
                        try:
                            lines.append(str(item[1][0]))
                        except (TypeError, IndexError):
                            continue
                if lines:
                    pages.append(f"[Page {page.number + 1}]\n" + "\n".join(lines))
        finally:
            doc.close()

        combined = "\n\n".join(pages).strip()
        if not combined:
            return failed_ir(ERROR_EMPTY, "OCR 未识别到文本", parser=self.name, report=pf)
        sections = split_text_into_sections(combined)
        return DocumentIR(parser=self.name, text=combined, sections=sections, nodes=[], report=pf)


# ---------------------------------------------------------------------
# Markdown / DOCX 通道（FileRouter 扩展）
# ---------------------------------------------------------------------

MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class MarkdownBackend:
    """Markdown 文档解析：标题层级保留原文（不做英中映射），$$ 公式与表格块保持原子。"""

    name = "markdown"

    def is_available(self) -> bool:
        return True

    def parse(self, pdf_path: Path, *, report: PreflightReport | None = None) -> DocumentIR:
        try:
            content = Path(pdf_path).read_text(encoding="utf-8")
        except OSError as exc:
            return failed_ir(ERROR_CORRUPT, f"Markdown 读取失败: {exc}", parser=self.name, report=report)

        if not content.strip():
            return failed_ir(ERROR_EMPTY, "Markdown 文件为空", parser=self.name, report=report)

        sections: list[tuple[str, str]] = []
        nodes: list[DocNode] = []
        current_title = "## 全文导读"
        current_lines: list[str] = []
        para_seq = 0

        def flush() -> None:
            nonlocal current_lines, para_seq
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((current_title, body))
                for unit_text, unit_type in split_content_units(body):
                    if not unit_text.strip():
                        continue
                    para_seq += 1
                    nodes.append(
                        DocNode(
                            node_id=f"{unit_type}-{para_seq:04d}",
                            type="heading" if unit_type == "text" and unit_text.startswith("#") else unit_type,
                            text=unit_text,
                            section_path=[current_title.lstrip("# ").strip()],
                        )
                    )
            current_lines = []

        for line in content.splitlines():
            heading = MD_HEADING_RE.match(line.strip())
            if heading:
                flush()
                level = min(4, len(heading.group(1)) + 1)
                current_title = f"{'#' * level} {heading.group(2).strip()}"
                nodes.append(
                    DocNode(
                        node_id=f"heading-{len(sections) + 1:04d}",
                        type="heading",
                        text=current_title,
                        level=level,
                        section_path=[heading.group(2).strip()],
                    )
                )
                continue
            current_lines.append(line)
        flush()

        return DocumentIR(
            parser=self.name,
            text=content.strip(),
            sections=sections or [("## 全文", content.strip())],
            nodes=nodes,
            report=report or PreflightReport(page_count=1, has_text_layer=True, parser_route="native"),
        )


class DocxBackend:
    """DOCX 解析（python-docx，守卫导入）：Heading 样式映射章节，表格转 Markdown。"""

    name = "docx"

    def is_available(self) -> bool:
        try:
            import docx  # noqa: F401
        except Exception:
            return False
        return True

    def parse(self, pdf_path: Path, *, report: PreflightReport | None = None) -> DocumentIR:
        try:
            import docx
        except Exception as exc:
            return failed_ir(ERROR_CORRUPT, f"python-docx 未安装: {exc}", parser=self.name, report=report)

        try:
            document = docx.Document(str(pdf_path))
        except Exception as exc:
            return failed_ir(ERROR_CORRUPT, f"DOCX 打开失败: {exc}", parser=self.name, report=report)

        nodes: list[DocNode] = []
        sections: list[tuple[str, str]] = []
        current_title = "## 全文导读"
        current_parts: list[str] = []

        def flush() -> None:
            nonlocal current_parts
            body = "\n\n".join(p for p in current_parts if p.strip())
            if body:
                sections.append((current_title, body))
            current_parts = []

        # 段落（按文档顺序，含 Heading 样式）
        for para in document.paragraphs:
            text = (para.text or "").strip()
            if not text:
                continue
            style_name = str(getattr(para.style, "name", "") or "")
            if style_name.lower().startswith("heading"):
                digits = re.findall(r"\d+", style_name)
                level = min(4, int(digits[0]) + 1) if digits else 2
                flush()
                current_title = f"{'#' * level} {text}"
                nodes.append(
                    DocNode(
                        node_id=f"heading-{len(nodes) + 1:04d}",
                        type="heading",
                        text=current_title,
                        level=level,
                        section_path=[text],
                    )
                )
                continue
            nodes.append(
                DocNode(
                    node_id=f"para-{len(nodes) + 1:04d}",
                    type="paragraph",
                    text=text,
                    section_path=[current_title.lstrip("# ").strip()],
                )
            )
            current_parts.append(text)

        # 表格节点（追加到末尾章节内容）
        for tbl_idx, table in enumerate(document.tables, start=1):
            rows = [[cell.text for cell in row.cells] for row in table.rows]
            markdown = _rows_to_markdown(rows)
            if not markdown:
                continue
            nodes.append(DocNode(node_id=f"table-{tbl_idx:04d}", type="table", text=markdown))
            current_parts.append(markdown)
        flush()

        text_all = "\n\n".join(content for _, content in sections).strip()
        if not text_all:
            return failed_ir(ERROR_EMPTY, "DOCX 未提取到内容", parser=self.name, report=report)
        return DocumentIR(
            parser=self.name,
            text=text_all,
            sections=sections,
            nodes=nodes,
            report=report or PreflightReport(page_count=1, has_text_layer=True, parser_route="native"),
        )


def parse_any_document(file_path: Path, settings: Any | None = None) -> DocumentIR:
    """FileRouter 统一入口：按扩展名分发（PDF -> ParserRouter；MD/DOCX -> 专用后端）。"""
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return ParserRouter(settings).parse(path)
    if suffix in {".md", ".markdown"}:
        return MarkdownBackend().parse(path)
    if suffix == ".docx":
        backend = DocxBackend()
        if not backend.is_available():
            return failed_ir(ERROR_CORRUPT, "DOCX 支持需要安装 python-docx", parser="docx")
        return backend.parse(path)
    return failed_ir(ERROR_CORRUPT, f"不支持的文件类型: {suffix or '(无扩展名)'}，支持 PDF/Markdown/DOCX")


# ---------------------------------------------------------------------
# ParserRouter：Preflight -> 引擎链 -> Quality Gate
# ---------------------------------------------------------------------


@dataclass
class RouterConfig:
    preferred: str = "auto"  # auto | pymupdf | pypdf | mineru
    mineru_enabled: bool = False
    ocr_enabled: bool = False
    min_chars_per_page: int = DEFAULT_MIN_CHARS_PER_PAGE
    quality_min_chars_per_page: float = 20.0
    quality_min_sections: int = 3

    @classmethod
    def from_settings(cls, settings: Any) -> RouterConfig:
        return cls(
            preferred=str(_cfg(settings, "parser_backend", "auto")).lower(),
            mineru_enabled=bool(_cfg(settings, "parser_mineru_enabled", False)),
            ocr_enabled=bool(_cfg(settings, "parser_ocr_enabled", False)),
            min_chars_per_page=int(_cfg(settings, "parser_min_chars_per_page", DEFAULT_MIN_CHARS_PER_PAGE)),
            quality_min_chars_per_page=float(_cfg(settings, "parser_quality_min_chars_per_page", 20.0)),
            quality_min_sections=int(_cfg(settings, "parser_quality_min_sections", 3)),
        )


@dataclass
class _ChainState:
    warnings: list[str] = field(default_factory=list)


class ParserRouter:
    """按 Preflight 结果路由解析引擎，Quality Gate 不达标自动降级。"""

    def __init__(self, settings: Any | None = None) -> None:
        self.config = RouterConfig.from_settings(settings)
        self._formula_recognizer = self._build_formula_recognizer(settings)
        self._layout_detector = self._build_layout_detector(settings)

    @staticmethod
    def _build_layout_detector(settings: Any | None) -> Any | None:
        """按配置构造版面检测器（layout_detector=off 时返回 None，回退字形启发式）。"""
        from .layout_detector import get_layout_detector

        return get_layout_detector(settings)

    @staticmethod
    def _build_formula_recognizer(settings: Any | None) -> Any | None:
        """按配置构造公式识别器（formula_backend=off 时返回 None，主通道零开销）。"""
        from .formula_recognizer import NullRecognizer, get_formula_recognizer

        recognizer = get_formula_recognizer(settings)
        return None if isinstance(recognizer, NullRecognizer) else recognizer

    def parse(self, pdf_path: Path) -> DocumentIR:
        report = preflight_pdf(
            pdf_path,
            min_chars_per_page=self.config.min_chars_per_page,
        )
        chain = self._build_chain(report)
        candidates: list[DocumentIR] = []

        for backend in chain:
            if not backend.is_available():
                continue
            try:
                ir = backend.parse(pdf_path, report=report)
            except Exception as exc:  # 单引擎崩溃不允许拖垮整条链
                report.warnings.append(f"{backend.name} 解析异常: {exc}")
                logger.warning("[ParserRouter] %s 解析异常: %s", backend.name, exc)
                continue
            if ir.ok and self._quality_ok(ir):
                if candidates:
                    report.warnings.append(f"已跳过低质量候选: {[c.parser for c in candidates]}")
                logger.info("[ParserRouter] ✅ %s 解析成功 | nodes=%s", backend.name, ir.node_stats())
                return ir
            if ir.ok:
                report.warnings.append(f"{backend.name} 质量门禁未达标，尝试下一引擎")
            candidates.append(ir)

        if candidates:
            ok_candidates = [c for c in candidates if c.ok]
            if ok_candidates:
                best = max(ok_candidates, key=lambda c: c.effective_chars)
                best.report = report
                return best
        return self._final_failure(report)

    def _build_chain(self, report: PreflightReport) -> list[ParserBackend]:
        mineru = MinerUBackend() if self.config.mineru_enabled else None
        pymupdf_backend = PyMuPDFBackend(
            formula_recognizer=self._formula_recognizer,
            layout_detector=self._layout_detector,
        )
        chain: list[ParserBackend] = []

        if self.config.preferred == "pypdf":
            return [PypdfBackend()]
        if self.config.preferred == "pymupdf":
            return [pymupdf_backend, PypdfBackend()]
        if self.config.preferred == "mineru" and mineru is not None:
            return [mineru, pymupdf_backend, PypdfBackend()]

        if report.parser_route == "scanned":
            if mineru is not None:
                chain.append(mineru)
            if self.config.ocr_enabled:
                chain.append(PaddleOCRBackend())
        chain.extend([pymupdf_backend, PypdfBackend()])
        return chain

    def _quality_ok(self, ir: DocumentIR) -> bool:
        pages = max(1, ir.report.page_count)
        chars_per_page = ir.effective_chars / pages
        enough_chars = chars_per_page >= self.config.quality_min_chars_per_page
        return enough_chars or len(ir.sections) >= self.config.quality_min_sections

    def _final_failure(self, report: PreflightReport) -> DocumentIR:
        if report.encrypted:
            return failed_ir(ERROR_ENCRYPTED, "PDF 已加密，所有解析引擎均失败", parser="router", report=report)
        if report.parser_route == "scanned":
            hint = "疑似扫描件，请安装并启用 MinerU（parser_mineru_enabled）或 PaddleOCR（parser_ocr_enabled）"
            return failed_ir(ERROR_SCANNED, hint, parser="router", report=report)
        if report.page_count == 0:
            return failed_ir(
                ERROR_CORRUPT, "无法读取任何页面，文件可能损坏或不是有效 PDF", parser="router", report=report
            )
        return failed_ir(ERROR_EMPTY, "所有解析引擎均未提取到有效文本", parser="router", report=report)


__all__ = [
    "FORMULA_LINE_MIN_CHARS",
    "FORMULA_LINE_RATIO",
    "MATH_CHAR_RE",
    "DocxBackend",
    "MarkdownBackend",
    "MinerUBackend",
    "PaddleOCRBackend",
    "ParserBackend",
    "ParserRouter",
    "PyMuPDFBackend",
    "PypdfBackend",
    "RouterConfig",
    "is_formula_text",
    "math_glyph_ratio",
    "parse_any_document",
]
