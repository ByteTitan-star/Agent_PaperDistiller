"""分词工具 — 支持中英文混合文本的 Tokenizer。

提供两种分词模式：
- tokenize(): 返回 set[str]，兼容旧代码（工具检索等）
- tokenize_list(): 返回 list[str]，保留词频信息，BM25 使用

中文策略：字符二元组（bigram），如 "后门攻击" → ["后门", "门攻", "攻击"]
英文策略：正则提取单词 + 小写
"""

from __future__ import annotations

import re

# 匹配连续中文字符
_ZH_RE = re.compile(r"[一-鿿]+")
# 匹配英文单词和数字
_EN_RE = re.compile(r"[a-zA-Z0-9]+")


def _extract_tokens(text: str) -> list[str]:
    """提取中英文 token 列表（保留词频信息）。

    中文：每 2 个连续汉字组成一个 bigram（"后门攻击" → ["后门", "门攻", "攻击"]）
    英文：提取单词并小写（"Backdoor Attack" → ["backdoor", "attack"]）

    Args:
        text: 待分词文本。

    Returns:
        list[str]: token 列表（可能有重复，保留词频）。
    """
    tokens: list[str] = []

    # 中文 bigram
    for zh_match in _ZH_RE.finditer(text):
        segment = zh_match.group()
        # 每 2 个连续汉字组成一个 bigram
        for i in range(len(segment) - 1):
            tokens.append(segment[i:i + 2])

    # 英文单词（小写）
    for en_match in _EN_RE.finditer(text):
        word = en_match.group().lower()
        # 过滤单字符（"a", "1" 等信息量太低）
        if len(word) >= 2:
            tokens.append(word)

    return tokens


def tokenize_list(text: str) -> list[str]:
    """分词，返回 list[str]（保留词频，BM25 使用）。

    Args:
        text: 待分词文本。

    Returns:
        list[str]: token 列表。
    """
    return _extract_tokens(text)


def tokenize(text: str) -> set[str]:
    """分词，返回 set[str]（去重，兼容旧代码）。

    Args:
        text: 待分词文本。

    Returns:
        set[str]: 去重后的 token 集合。
    """
    return set(_extract_tokens(text))
