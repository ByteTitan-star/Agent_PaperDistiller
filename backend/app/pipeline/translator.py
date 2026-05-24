# 	在线翻译、文本切分
import json
import re
import time
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .common_utils import remove_surrogates


def normalize_language_code(target_language: str) -> str:
    """
    【语言代码标准化】
    将用户输入的语言名称标准化为翻译服务代码。

    支持映射：
    - 中文/简体中文/chinese/zh/zh-cn -> zh-CN
    - 英文/english -> en
    - 日文/japanese/ja -> ja

    参数:
        target_language: 用户输入的语言名称

    返回:
        标准化的语言代码（默认 zh-CN）
    """
    low = target_language.strip().lower()
    mapping = {
        "中文": "zh-CN",
        "简体中文": "zh-CN",
        "chinese": "zh-CN",
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "英文": "en",
        "english": "en",
        "日文": "ja",
        "japanese": "ja",
        "ja": "ja",
    }
    return mapping.get(low, "zh-CN")


def translate_text_online(text: str, target_language: str, timeout: int = 20) -> str:
    """
    【在线文本翻译】
    调用 Google 翻译 API 翻译单段文本。

    注意：
    - 使用非官方 API（translate.googleapis.com），可能有频率限制
    - 目标语言为英文时直接返回原文（无需翻译）
    - 自动清理代理字符

    参数:
        text: 待翻译文本
        target_language: 目标语言
        timeout: 请求超时时间（秒）

    返回:
        翻译后的文本，失败则返回原文
    """
    clean = remove_surrogates(text).strip()
    if not clean:
        return clean

    target_code = normalize_language_code(target_language)
    if target_code.startswith("en"):
        return clean

    params = urlencode(
        {
            "client": "gtx",
            "sl": "auto",
            "tl": target_code,
            "dt": "t",
            "q": clean,
        }
    )
    url = f"https://translate.googleapis.com/translate_a/single?{params}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    with urlopen(request, timeout=timeout) as response:
        payload: Any = json.loads(response.read().decode("utf-8"))

    if not payload or not payload[0]:
        return clean

    translated_parts = [part[0] for part in payload[0] if part and part[0]]
    translated = "".join(translated_parts).strip()
    return translated or clean


def split_for_translation(text: str, max_chars: int = 500) -> list[str]:
    """
    【文本切分用于翻译】
    把长文本拆成多个短段，避免单次翻译请求过长。

    切分策略：
    - 优先按句子边界切分（句号、问号、感叹号）
    - 超长句子（>max_chars）强制截断
    - 尽量保持语义完整

    参数:
        text: 原始长文本
        max_chars: 每段最大字符数（默认 500）

    返回:
        切分后的文本段列表
    """
    normalized = remove_surrogates(text).strip()
    if not normalized:
        return []

    units = re.split(r"(?<=[。！？.!?])\s+|\n+", normalized)
    parts: list[str] = []
    current = ""

    for unit in units:
        u = unit.strip()
        if not u:
            continue

        if len(u) > max_chars:
            if current:
                parts.append(current)
                current = ""
            for start in range(0, len(u), max_chars):
                parts.append(u[start : start + max_chars])
            continue

        if not current:
            current = u
            continue

        if len(current) + 1 + len(u) <= max_chars:
            current = f"{current} {u}"
        else:
            parts.append(current)
            current = u

    if current:
        parts.append(current)

    return parts


def translate_long_text(text: str, target_language: str) -> tuple[str, int]:
    """
    【长文本翻译】
    翻译长文本并统计失败段数。

    处理流程：
    1. 切分为短段
    2. 逐段翻译，间隔 30ms 避免频率限制
    3. 失败段落标记为"[翻译失败，保留原文]"

    参数:
        text: 待翻译的长文本
        target_language: 目标语言

    返回:
        (翻译后文本, 失败段数)
    """
    pieces = split_for_translation(text)
    if not pieces:
        return "", 0

    translated_parts: list[str] = []
    failures = 0

    for piece in pieces:
        try:
            translated_parts.append(translate_text_online(piece, target_language))
            time.sleep(0.03)
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
            failures += 1
            translated_parts.append(f"[翻译失败，保留原文] {piece}")

    return "\n".join(translated_parts).strip(), failures


def translate_sections(
    sections: list[tuple[str, str]],
    target_language: str,
) -> tuple[list[tuple[str, str]], int]:
    """
    【章节翻译】
    按章节翻译全文内容，保留章节结构。

    参数:
        sections: 章节列表 [(章节标题, 章节内容), ...]
        target_language: 目标语言

    返回:
        (翻译后章节列表, 总失败段数)
    """
    target_code = normalize_language_code(target_language)
    if target_code.startswith("en"):
        return sections, 0

    translated_sections: list[tuple[str, str]] = []
    failures = 0
    for title, content in sections:
        translated_content, failed_count = translate_long_text(content, target_language)
        failures += failed_count
        translated_sections.append((title, translated_content or content))
    return translated_sections, failures


def flatten_sections_to_chunks(sections: list[tuple[str, str]], max_chunks: int = 40) -> list[str]:
    """
    【章节打平为块】
    把章节内容打平为短句切片，供摘要/证据提取使用。

    处理逻辑：
    - 按句子边界切分
    - 每句截取前 280 字符
    - 最多返回 max_chunks 个片段

    参数:
        sections: 章节列表
        max_chunks: 最大片段数（默认 40）

    返回:
        短句切片列表
    """
    chunks: list[str] = []
    for _, content in sections:
        compact = remove_surrogates(content).strip()
        if not compact:
            continue
        units = re.split(r"(?<=[。！？.!?])\s+|\n+", compact)
        for unit in units:
            item = unit.strip()
            if not item:
                continue
            chunks.append(item[:280])
            if len(chunks) >= max_chunks:
                return chunks
        if len(chunks) >= max_chunks:
            break
    return chunks


def translate_chunks(chunks: list[str], target_language: str) -> tuple[list[str], int]:
    """
    【短句列表翻译】
    翻译短句列表，带缓存与失败回退。

    优化点：
    - 使用字典缓存避免重复翻译相同句子
    - 失败时保留原文并标记
    - 间隔 30ms 避免频率限制

    参数:
        chunks: 短句列表
        target_language: 目标语言

    返回:
        (翻译后列表, 失败段数)
    """
    if not chunks:
        return [], 0

    target_code = normalize_language_code(target_language)
    if target_code.startswith("en"):
        return chunks, 0

    translated_chunks: list[str] = []
    cache: dict[str, str] = {}
    failures = 0

    for chunk in chunks:
        if not chunk.strip():
            translated_chunks.append(chunk)
            continue

        cached = cache.get(chunk)
        if cached is not None:
            translated_chunks.append(cached)
            continue

        try:
            translated = translate_text_online(chunk, target_language)
            cache[chunk] = translated
            translated_chunks.append(translated)
            time.sleep(0.03)
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError):
            failures += 1
            fallback = f"[翻译失败，保留原文] {chunk}"
            cache[chunk] = fallback
            translated_chunks.append(fallback)

    return translated_chunks, failures


__all__ = [
    "normalize_language_code",
    "translate_text_online",
    "split_for_translation",
    "translate_long_text",
    "translate_sections",
    "flatten_sections_to_chunks",
    "translate_chunks",
]