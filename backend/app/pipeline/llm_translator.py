"""LLM 翻译通道：替代 Google 非官方接口，学术术语与 LaTeX 公式不毁。

约束（写入 system prompt）：
- `$...$` / `$$...$$` 公式原样保留；
- Markdown 表格 / 列表 / [Page N] 标记结构不变；
- 专业术语（MNIST、ResNet、ASR 等）保留英文。

失败语义：单段失败回退原文并计入 failures，不抛异常拖垮整条管线。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .document_parser import split_content_units
from .translator import normalize_language_code, split_for_translation

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a professional academic paper translation engine.
Translate the user's text into {language}.
Rules (MUST follow):
1. Keep LaTeX formulas between $...$ or $$...$$ EXACTLY as-is. Never translate, split, or modify them.
2. Preserve Markdown structure: tables, lists, headings, and [Page N] markers.
3. Keep technical terms, model names, and dataset names in their original language (e.g. MNIST, ResNet, ASR).
4. Output ONLY the translated text, with no explanations or extra formatting."""


def _cfg(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None)
    return default if value is None else value


def _language_display_name(target_language: str) -> str:
    code = normalize_language_code(target_language)
    return {"zh-CN": "Simplified Chinese", "en": "English", "ja": "Japanese"}.get(code, "Simplified Chinese")


async def _translate_piece(
    client: Any,
    model: str,
    piece: str,
    language_name: str,
    temperature: float,
) -> str:
    response = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(language=language_name)},
            {"role": "user", "content": piece},
        ],
    )
    content = (response.choices[0].message.content or "").strip() if response.choices else ""
    _log_usage(response, model)
    return content or piece


def _log_usage(response: Any, model: str) -> None:
    """尽力记录 token 用量（失败静默，不影响翻译主流程）。"""
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        from ..services.token_logger import log_token_to_db

        asyncio.get_running_loop().create_task(
            log_token_to_db(
                None,
                model,
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0),
                "translation",
            )
        )
    except Exception:  # pragma: no cover - 记账失败不影响翻译
        pass


async def translate_text_llm(
    text: str,
    target_language: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 60.0,
    max_chars: int = 2000,
) -> str:
    """LLM 翻译单段长文本（内部自动分片），失败分片回退原文。"""
    from openai import AsyncOpenAI

    language_name = _language_display_name(target_language)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    pieces = split_for_translation(text, max_chars=max_chars)
    translated: list[str] = []
    for piece in pieces:
        try:
            translated.append(await _translate_piece(client, model, piece, language_name, temperature=0.1))
        except Exception as exc:
            logger.warning("[LLM翻译] ⚠️ 分片翻译失败，回退原文: %s", exc)
            translated.append(piece)
    return "\n".join(translated).strip()


async def translate_sections_llm(
    sections: list[tuple[str, str]],
    target_language: str,
    settings: Any = None,
) -> tuple[list[tuple[str, str]], int]:
    """LLM 按章节翻译（受限并发），返回 (翻译后章节, 失败段数)。

    单段失败回退原文（与 Google 通道语义一致），整体异常由调用方回退 Google。
    """
    from openai import AsyncOpenAI

    api_key = str(_cfg(settings, "deepseek_api_key", "your-api-key"))
    base_url = str(_cfg(settings, "deepseek_base_url", "https://api.deepseek.com"))
    model = str(_cfg(settings, "deepseek_model", "deepseek-chat"))
    timeout = float(_cfg(settings, "deepseek_timeout_sec", 60.0))
    concurrency = max(1, int(_cfg(settings, "translation_llm_concurrency", 4)))
    max_chars = max(500, int(_cfg(settings, "translation_llm_max_chars", 2000)))

    if not api_key or api_key == "your-api-key":
        raise RuntimeError("LLM 翻译需要配置 deepseek_api_key")

    language_name = _language_display_name(target_language)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    semaphore = asyncio.Semaphore(concurrency)
    failures = 0

    async def translate_section(title: str, content: str) -> tuple[str, str]:
        nonlocal failures
        results: list[str] = []
        async with semaphore:
            # 公式块原样保留不送翻译；表格整块送翻；正文按句分片
            for unit_text, unit_type in split_content_units(content):
                if unit_type == "formula":
                    results.append(unit_text)
                    continue
                if unit_type == "table":
                    pieces = [unit_text] if len(unit_text) <= max_chars else _split_table_for_llm(unit_text, max_chars)
                else:
                    pieces = split_for_translation(unit_text, max_chars=max_chars)
                for piece in pieces:
                    if not piece.strip():
                        continue
                    try:
                        results.append(await _translate_piece(client, model, piece, language_name, temperature=0.1))
                    except Exception as exc:
                        failures += 1
                        logger.warning("[LLM翻译] ⚠️ 段落翻译失败，回退原文: %s", exc)
                        results.append(piece)
        return title, "\n\n".join(results).strip()

    translated = await asyncio.gather(*(translate_section(t, c) for t, c in sections))
    return list(translated), failures


def _split_table_for_llm(table: str, max_chars: int) -> list[str]:
    """超长表格按行组切分送翻（重复表头，保持 Markdown 结构可还原）。"""
    lines = table.splitlines()
    if len(lines) <= 2:
        return [table]
    header, body = lines[:2], lines[2:]
    header_text = "\n".join(header)
    pieces: list[str] = []
    current = [header_text]
    current_len = len(header_text)
    for row in body:
        if current_len + len(row) + 1 > max_chars and len(current) > 1:
            pieces.append("\n".join(current))
            current = [header_text]
            current_len = len(header_text)
        current.append(row)
        current_len += len(row) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


__all__ = ["SYSTEM_PROMPT", "translate_sections_llm", "translate_text_llm"]
