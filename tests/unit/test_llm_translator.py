"""LLM 翻译通道测试（mock OpenAI 客户端，不依赖真实 API）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.llm_translator import _split_table_for_llm, translate_sections_llm


def _settings(**overrides: object) -> SimpleNamespace:
    defaults = {
        "deepseek_api_key": "test-key",
        "deepseek_base_url": "https://api.example.com",
        "deepseek_model": "test-model",
        "deepseek_timeout_sec": 5.0,
        "translation_llm_concurrency": 2,
        "translation_llm_max_chars": 1000,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_client() -> tuple[MagicMock, list[str]]:
    """返回 (client, 已翻译的 user 消息收集器)。"""
    client = MagicMock()
    sent: list[str] = []

    async def fake_create(**kwargs: object) -> SimpleNamespace:
        messages = kwargs.get("messages", [])  # type: ignore[arg-type]
        sent.append(str(messages[-1]["content"]))
        message = SimpleNamespace(content=f"译文<{len(sent)}>")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    client.chat.completions.create = AsyncMock(side_effect=fake_create)
    return client, sent


@pytest.mark.asyncio
async def test_translate_sections_llm_passthrough_formula() -> None:
    """公式块原样保留，不送 LLM。"""
    client, sent = _mock_client()
    sections = [("## 3 方法", "定义如下：\n\n$$E=mc^2$$\n\n其中 E 是能量。")]
    with patch("openai.AsyncOpenAI", return_value=client):
        translated, failures = await translate_sections_llm(sections, "Chinese", _settings())

    assert failures == 0
    content = translated[0][1]
    assert "$$E=mc^2$$" in content  # 公式原样保留
    assert all("$$" not in s for s in sent)  # 公式没有送进 LLM


@pytest.mark.asyncio
async def test_translate_sections_llm_table_sent_whole() -> None:
    """Markdown 表格整块送翻（保持结构）。"""
    client, sent = _mock_client()
    table = "| Model | Acc |\n| --- | --- |\n| A | 91.2 |"
    sections = [("## 4 实验", f"结果：\n\n{table}")]
    with patch("openai.AsyncOpenAI", return_value=client):
        translated, failures = await translate_sections_llm(sections, "Chinese", _settings())

    assert failures == 0
    table_pieces = [s for s in sent if s.startswith("| Model")]
    assert len(table_pieces) == 1  # 表格未被切散
    assert any("| A | 91.2 |" in s for s in sent)


@pytest.mark.asyncio
async def test_translate_sections_llm_failure_falls_back_to_original() -> None:
    """单段失败回退原文并计入 failures，不抛异常。"""
    client = MagicMock()

    async def boom(**kwargs: object) -> SimpleNamespace:
        raise RuntimeError("api down")

    client.chat.completions.create = AsyncMock(side_effect=boom)
    sections = [("## 1 引言", "Original english text.")]
    with patch("openai.AsyncOpenAI", return_value=client):
        translated, failures = await translate_sections_llm(sections, "Chinese", _settings())

    assert failures >= 1
    assert translated[0][1] == "Original english text."


@pytest.mark.asyncio
async def test_translate_sections_llm_requires_api_key() -> None:
    with pytest.raises(RuntimeError, match="deepseek_api_key"):
        await translate_sections_llm([("## 1", "text")], "Chinese", _settings(deepseek_api_key="your-api-key"))


def test_split_table_for_llm_repeats_header() -> None:
    rows = "\n".join(f"| M{i} | {i} |" for i in range(60))
    table = "| Model | Score |\n| --- | --- |\n" + rows
    pieces = _split_table_for_llm(table, max_chars=200)
    assert len(pieces) >= 2
    assert all("| Model | Score |" in p for p in pieces)
