# PDF解析、章节切分、标题识别
import re
from pathlib import Path

from pypdf import PdfReader

from .common_utils import remove_surrogates


# 章节名映射表（英 -> 中）
# 用于将英文论文中的章节标题自动转换为中文
EN_TO_CN_SECTION_MAP: dict[str, str] = {
    "ABSTRACT": "摘要",
    "INTRODUCTION": "引言",
    "RELATED WORK": "相关工作",
    "BACKGROUND": "背景",
    "METHOD": "方法",
    "METHODOLOGY": "方法",
    "APPROACH": "方法",
    "EXPERIMENT": "实验",
    "EXPERIMENTS": "实验",
    "EVALUATION": "实验",
    "RESULTS": "结果",
    "DISCUSSION": "讨论",
    "CONCLUSION": "结论",
    "CONCLUSIONS": "结论",
    "CONCLUSION AND FUTURE WORK": "结论与未来工作",
    "CONCLUSION AND FUTURE WORKS": "结论与未来工作",
    "FUTURE WORK": "未来工作",
    "FUTURE WORKS": "未来工作",
    "REFERENCES": "参考文献",
    "APPENDIX": "附录",
}

# 中文章节标题关键词集合
# 用于识别 PDF 中已存在的中文标题
CN_HEADINGS = {
    "摘要",
    "引言",
    "相关工作",
    "背景",
    "方法",
    "实验",
    "结果",
    "讨论",
    "结论",
    "参考文献",
    "附录",
    "未来工作",
}

# 提取 PDF 文本时页标记（如 `[Page 3]`）的识别规则。
PAGE_MARK_RE = re.compile(r"^\[Page\s+\d+\]\s*", re.IGNORECASE)

# 英文章节标题的正则匹配模式
EN_HEADING_PATTERN = "|".join(
    re.escape(token) for token in sorted(EN_TO_CN_SECTION_MAP.keys(), key=len, reverse=True)
)

# 带编号的章节标题匹配规则（如 "1. Introduction" 或 "2.3 Method"）
NUMBERED_TOKEN_HEADING_RE = re.compile(
    rf"^(?:(\d+(?:\.\d+){{0,3}}|[A-Z](?:\.\d+){{1,3}}|[IVXLC]+)[\.\)]?\s+)?({EN_HEADING_PATTERN})\b[:.\-]?\s*(.*)$"
)

# 全大写标题匹配规则（用于识别格式不规范的标题）
NUMBERED_CAPS_HEADING_RE = re.compile(
    r"^(\d+(?:\.\d+){0,3}|[A-Z](?:\.\d+){1,3})\s+([A-Z][A-Z0-9/&\-\(\)]*(?:\s+[A-Z][A-Z0-9/&\-\(\)]*){0,15})(?:\s+(.*))?$"
)


# ---------------------------------------------------------------------
# PDF 解析与分块 (PDF Parsing & Chunking)
# ---------------------------------------------------------------------


def extract_text_from_pdf(pdf_path: Path) -> str:
    """
    【PDF 文本提取】
    从 PDF 文件中提取纯文本，并为每页内容添加页码标记。

    功能说明：
    - 使用 pypdf 库逐页提取文本
    - 自动清理 UTF-16 代理字符
    - 为每页添加 [Page N] 标记，便于后续定位

    参数:
        pdf_path: PDF 文件的 Path 对象路径

    返回:
        提取的完整文本，包含页码标记；如果失败则返回错误提示
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
    【文本分块】
    按固定窗口大小将长文本切分为多个重叠的文本块。

    用途：
    - 将长论文切分为适合 LLM 处理的小块
    - 重叠设计确保上下文连续性

    参数:
        text: 待切分的原始文本
        chunk_size: 每个文本块的最大字符数
        overlap: 相邻块之间的重叠字符数

    返回:
        文本块列表，每个块都是字符串
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


def normalize_heading_name(name: str) -> str:
    """
    【章节名标准化】
    将英文章节名映射为对应的中文章节名。

    处理逻辑：
    - 去除多余空格并转为大写
    - 在 EN_TO_CN_SECTION_MAP 中查找对应中文
    - 未找到则保留原名

    参数:
        name: 章节标题（英文或中文）

    返回:
        标准化后的中文标题
    """
    token = re.sub(r"\s+", " ", name).strip().upper()
    return EN_TO_CN_SECTION_MAP.get(token, name.strip())


def normalize_heading_line(text: str) -> str:
    """
    【标题行规整】
    规整章节行文本，修复 PDF 抽取产生的断裂大写单词。

    问题场景：
    - PDF 提取时 "RELATED WORK" 可能被分割为 "R E L A T E D  W O R K"
    - 本函数通过正则合并被空格分隔的单个大写字母

    参数:
        text: 原始标题行文本

    返回:
        修复后的连续文本
    """
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""

    prev = ""
    while normalized != prev:
        prev = normalized
        normalized = re.sub(r"\b([A-Z])\s+([A-Z]{2,})\b", r"\1\2", normalized)
    return normalized


def parse_section_heading(line: str) -> tuple[str, str] | None:
    """
    【章节标题解析】
    解析单行文本，判断是否为章节标题。

    识别能力：
    - 带编号的英文标题（如 "1. Introduction"）
    - 全大写的英文标题
    - 纯中文标题（如 "摘要"、"引言"）
    - 自动转换为 Markdown 标题格式（## ###）

    参数:
        line: 待检测的单行文本

    返回:
        如果是标题，返回 (markdown_heading, 剩余内容)；否则返回 None
    """
    cleaned = normalize_heading_line(PAGE_MARK_RE.sub("", line))
    if not cleaned:
        return None

    upper_cleaned = cleaned.upper()
    token_match = NUMBERED_TOKEN_HEADING_RE.match(upper_cleaned)
    if token_match:
        prefix = (token_match.group(1) or "").strip()
        heading_token = token_match.group(2).strip()
        remainder = (cleaned[token_match.start(3) : token_match.end(3)] or "").strip()

        if remainder and not prefix and heading_token != "ABSTRACT" and remainder[0].islower():
            return None

        level = min(4, prefix.count(".") + 2) if prefix else 2
        heading_cn = normalize_heading_name(heading_token)
        heading_title = f"{prefix} {heading_cn}".strip()
        return f"{'#' * level} {heading_title}", remainder

    numeric_match = NUMBERED_CAPS_HEADING_RE.match(cleaned)
    if numeric_match:
        number = numeric_match.group(1).strip()
        title_raw = numeric_match.group(2).strip(" -:.")
        remainder = (numeric_match.group(3) or "").strip()

        if number.isdigit() and len(number) > 2:
            return None

        too_long = len(title_raw.split()) > 14
        if title_raw and len(title_raw) <= 90 and not too_long:
            level = min(4, number.count(".") + 2)
            title_cn = normalize_heading_name(title_raw)
            return f"{'#' * level} {number} {title_cn}", remainder

    cn_match = re.match(
        r"^(摘要|引言|相关工作|背景|方法|实验|结果|讨论|结论|参考文献|附录|未来工作)\b[:：]?\s*(.*)$",
        cleaned,
    )
    if cn_match:
        heading_cn = cn_match.group(1)
        remainder = (cn_match.group(2) or "").strip()
        return f"## {heading_cn}", remainder

    return None


def split_text_into_sections(text: str) -> list[tuple[str, str]]:
    """
    【章节切分】
    将完整论文文本按章节切分为 (标题, 内容) 的列表。

    处理流程：
    1. 逐行扫描，识别章节标题
    2. 按标题将内容分组
    3. 合并过短的片段到上一章节
    4. 确保至少返回一个默认章节

    参数:
        text: 完整的论文文本

    返回:
        (章节标题, 章节内容) 的元组列表
    """
    prepared = remove_surrogates(text)
    lines = [line.rstrip() for line in prepared.splitlines()]
    sections: list[tuple[str, str]] = []

    current_title = "## 全文导读"
    current_lines: list[str] = []

    def flush_section() -> None:
        """【内部函数】将当前累积的内容刷新到 sections 列表中"""
        nonlocal current_lines, current_title
        content = "\n".join(current_lines).strip()
        if content:
            sections.append((current_title, content))
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        heading_info = parse_section_heading(stripped)
        if heading_info:
            flush_section()
            current_title, remainder = heading_info
            if remainder:
                current_lines.append(remainder)
            continue

        current_lines.append(stripped)

    flush_section()

    if not sections:
        normalized = remove_surrogates(text).strip()
        if normalized:
            sections.append(("## 全文", normalized))

    merged: list[tuple[str, str]] = []
    for title, content in sections:
        compact = re.sub(r"\s+", " ", content).strip()
        if not compact:
            continue
        if merged and title == merged[-1][0]:
            prev_title, prev_content = merged[-1]
            merged[-1] = (prev_title, f"{prev_content}\n{content}".strip())
            continue
        if len(compact) < 80 and merged:
            prev_title, prev_content = merged[-1]
            merged[-1] = (prev_title, f"{prev_content}\n{compact}".strip())
            continue
        merged.append((title, content))

    return merged or [("## 全文", remove_surrogates(text).strip())]


__all__ = [
    "EN_TO_CN_SECTION_MAP",
    "CN_HEADINGS",
    "PAGE_MARK_RE",
    "extract_text_from_pdf",
    "chunk_text",
    "normalize_heading_name",
    "normalize_heading_line",
    "parse_section_heading",
    "split_text_into_sections",
]