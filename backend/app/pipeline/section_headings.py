"""章节标题识别与章节切分（正则通道）。

从 document_parser.py 拆出：解析后端（parser_backend）与兼容入口（document_parser）
都需要这些规则，拆成独立模块避免循环导入。
标题识别的字号通道在 parser_backend.PyMuPDFBackend 中，本模块是其正则兜底。
"""

from __future__ import annotations

import re

from .common_utils import remove_surrogates

# 章节名映射表（英 -> 中）
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
EN_HEADING_PATTERN = "|".join(re.escape(token) for token in sorted(EN_TO_CN_SECTION_MAP.keys(), key=len, reverse=True))

# 带编号的章节标题匹配规则（如 "1. Introduction" 或 "2.3 Method"）
NUMBERED_TOKEN_HEADING_RE = re.compile(
    rf"^(?:(\d+(?:\.\d+){{0,3}}|[A-Z](?:\.\d+){{1,3}}|[IVXLC]+)[\.\)]?\s+)?({EN_HEADING_PATTERN})\b[:.\-]?\s*(.*)$"
)

# 全大写标题匹配规则（用于识别格式不规范的标题）
NUMBERED_CAPS_HEADING_RE = re.compile(
    r"^(\d+(?:\.\d+){0,3}|[A-Z](?:\.\d+){1,3})\s+([A-Z][A-Z0-9/&\-\(\)]*(?:\s+[A-Z][A-Z0-9/&\-\(\)]*){0,15})(?:\s+(.*))?$"
)

# 参考文献章节判定（中英）
REFERENCES_HEADING_RE = re.compile(
    r"^(#{2,4}\s+)?(\d+[\.\)]?\s*)?(REFERENCES|参考文献|BIBLIOGRAPHY)\s*$", re.IGNORECASE
)

# 图/表题注匹配（用于图注绑定）
CAPTION_RE = re.compile(r"^\s*((?:Table|Fig\.?|Figure|图|表)\s*[A-Za-z0-9]+\s*[:.．：])\s*(.*)$", re.IGNORECASE)


def is_references_section(heading: str) -> bool:
    """判断章节标题是否为参考文献章节（用于检索时默认排除）。"""
    return bool(REFERENCES_HEADING_RE.match(heading.strip()))


def normalize_heading_name(name: str) -> str:
    """将英文章节名映射为对应的中文章节名，未命中则保留原名。"""
    token = re.sub(r"\s+", " ", name).strip().upper()
    return EN_TO_CN_SECTION_MAP.get(token, name.strip())


def normalize_heading_line(text: str) -> str:
    """规整章节行文本，修复抽取产生的断裂大写单词（如 "R E L A T E D"）。"""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""

    prev = ""
    while normalized != prev:
        prev = normalized
        normalized = re.sub(r"\b([A-Z])\s+([A-Z]{2,})\b", r"\1\2", normalized)
    return normalized


def parse_section_heading(line: str) -> tuple[str, str] | None:
    """解析单行文本是否为章节标题；命中返回 (markdown_heading, 剩余内容)。"""
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
    """将完整论文文本按章节切分为 (Markdown 标题, 内容) 列表（正则通道）。"""
    prepared = remove_surrogates(text)
    lines = [line.rstrip() for line in prepared.splitlines()]
    sections: list[tuple[str, str]] = []

    current_title = "## 全文导读"
    current_lines: list[str] = []

    def flush_section() -> None:
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
    "CAPTION_RE",
    "CN_HEADINGS",
    "EN_HEADING_PATTERN",
    "EN_TO_CN_SECTION_MAP",
    "NUMBERED_CAPS_HEADING_RE",
    "NUMBERED_TOKEN_HEADING_RE",
    "PAGE_MARK_RE",
    "REFERENCES_HEADING_RE",
    "is_references_section",
    "normalize_heading_line",
    "normalize_heading_name",
    "parse_section_heading",
    "split_text_into_sections",
]
