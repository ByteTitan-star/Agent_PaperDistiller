"""分词工具 — 中英文混合文本的统一 analyzer（供 BM25 检索使用）。

设计原则（生产级 analyzer 链）：
  tokenizer → lowercase → 停用词过滤 → stemmer
并且 **索引和查询必须用同一个 analyzer**，否则 term 对不上。

- 中文：用 **jieba** 分词（词典 + HMM），不再用 bigram（粒度粗、有噪音）。
  jieba 能处理中英混排，但只切词、不做词形还原；中文无词形变化，无需 stem。
- 英文：regex 抓单词 → lowercase → 过滤停用词 → **Snowball(Porter) stem**
  （running→run、attacks→attack），做词干还原以提升召回。

对外两个 API（保留旧签名，调用方无需改动）：
- ``tokenize_list(text) -> list[str]``：保留词频，BM25 用（TF 项要数词频）。
- ``tokenize(text) -> set[str]``：去重，兼容旧代码。
"""

from __future__ import annotations

import re

import jieba
from snowballstemmer import stemmer as _snowball_stemmer

# 连续中文区间（用于判断 jieba 切出的 token 是否含 CJK）
_CJK_RE = re.compile(r"[一-鿿]")
# 英文/数字单词
_EN_WORD_RE = re.compile(r"[a-zA-Z0-9]+")

# 英文停用词表（标准 Lucene English stop words 子集 + 常见疑问/连接词）
_EN_STOPWORDS = frozenset(
    """
    a an and are as at be been being but by for from had has have he her him his
    how i if in into is it its me my no not of on or our she so such that the
    their them then there these they this to was we were what when where which
    who will with you your us ours do does did doing done can could should would
    may might must shall about above after again all also am any because before
    below between both down during each few further here more most nor only other
    over own same some than too under up very while why yes no
    """.split()
)

# 中文停用词（高频功能词，信息量低）
_CN_STOPWORDS = frozenset(
    "的 了 在 是 和 与 或 也 就 都 而 及 以 等 这 那 这些 那些 他 她 它 我 你 你们 我们 "
    "把 被 让 给 对 从 向 往 到 于 之 的话 一下 一种 一个 一些 不 没 没有 不是 什么 怎么 如何 "
    "可以 进行 通过 根据 由于 因为 所以 但是 不过 然而 如果 然后 还 再 已 已经 将 着 过 地 得".split()
)

# 英文 stemmer（Snowball / Porter 算法，纯 Python，无需下载词典数据）
_en_stemmer = _snowball_stemmer("english")


def _normalize_en_token(word: str) -> str | None:
    """英文 token 归一化：小写 → 去停用词 → stem。返回 None 表示丢弃。"""
    w = word.lower()
    if len(w) < 2 or w in _EN_STOPWORDS:
        return None
    if w.isdigit():
        # 纯数字不做 stem（某些 stem 实现会截断数字），保险起见跳过
        return w
    return _en_stemmer.stemWord(w)


def _extract_tokens(text: str) -> list[str]:
    """提取中英文 token 列表（保留词频）。

    流程：jieba.lcut 切分（自动处理中英混排）→ 对每个 token 按类型归一化：
      - 含中文：直接作为 term（中文无需 stem），丢停用词；
      - 纯英文/数字：进一步 regex 拆成单词，逐个 lowercase+stem+去停用词。
    """
    tokens: list[str] = []
    for raw in jieba.lcut(text):
        raw = raw.strip()
        if not raw:
            continue
        if _CJK_RE.search(raw):
            # 中文 token：jieba 已切好，去停用词即可（中文无词形/大小写问题）
            if raw in _CN_STOPWORDS:
                continue
            tokens.append(raw)
        else:
            # 英文/数字/标点：拆成单词后归一化
            for word in _EN_WORD_RE.findall(raw):
                nw = _normalize_en_token(word)
                if nw:
                    tokens.append(nw)
    return tokens


def tokenize_list(text: str) -> list[str]:
    """分词，返回 list[str]（保留词频，BM25 用）。"""
    if not text:
        return []
    return _extract_tokens(text)


def tokenize(text: str) -> set[str]:
    """分词，返回 set[str]（去重，兼容旧代码）。"""
    return set(_extract_tokens(text))
