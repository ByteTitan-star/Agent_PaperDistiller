import json
import re
import time
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .common_utils import remove_surrogates


def normalize_language_code(target_language: str) -> str:
    """将用户输入语言名标准化为翻译服务代码。"""
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
    """调用在线翻译接口翻译单段文本。"""
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

    with urlopen(request, timeout=timeout) as response:  # type: ignore[call-arg]
        payload: Any = json.loads(response.read().decode("utf-8"))

    if not payload or not payload[0]:
        return clean

    translated_parts = [part[0] for part in payload[0] if part and part[0]]
    translated = "".join(translated_parts).strip()
    return translated or clean


def split_for_translation(text: str, max_chars: int = 500) -> list[str]:
    """把长文本拆成多个短段，避免单次翻译请求过长。"""
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
    """翻译长文本并统计失败段数。"""
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
    """按章节翻译全文内容，返回翻译后章节与失败计数。"""
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
    """把章节内容打平为短句切片，供摘要/证据提取使用。"""
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
    """翻译短句列表，带缓存与失败回退。"""
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

