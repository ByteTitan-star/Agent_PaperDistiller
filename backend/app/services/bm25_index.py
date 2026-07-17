"""基于 ``rank_bm25`` 的 BM25 倒排索引封装。

相比原来 ``chat.py`` 里手写的"每次查询全量扫描所有 chunks"，这里用 rank_bm25 的
``BM25Okapi``：它在构造时建一次倒排索引（预计算 df/avgdl/idf），查询时只扫命中倒排表的
文档，复杂度从 O(N×chunks) 降到 O(命中数×query词数)。

典型用法：
    idx = BM25Index(chunks)        # 论文入库时建一次
    hits = idx.search(query, 10)   # 查询多次复用同一个 idx

缓存与失效由 ``Storage.get_bm25_index`` 管理（按 paper_id 缓存，``save_chunks`` 失效）。
"""

from __future__ import annotations

import logging
from typing import Any

from .token_utils import tokenize_list

logger = logging.getLogger("services.bm25_index")


class BM25Index:
    """单篇论文 chunks 的 BM25 倒排索引。

    构造时对每个 chunk 分词并喂给 rank_bm25 建 index；查询时对 query 分词后取打分 top-k。
    chunks 与内部 tokenized 语料一一对应（过滤掉空块后对齐）。
    """

    def __init__(self, chunks: list[str]) -> None:
        # 过滤空块，并保持 chunk 文本与 tokenized 的下标对齐
        self._chunks: list[str] = [c for c in chunks if c and c.strip()]
        self._tokenized: list[list[str]] = [tokenize_list(c) for c in self._chunks]

        # 空语料时 rank_bm25 会抛错，这里留空标志，search 直接返回空
        if self._tokenized:
            from rank_bm25 import BM25Okapi

            self._bm25: Any = BM25Okapi(self._tokenized)
        else:
            self._bm25 = None
        logger.debug("BM25Index 建立完成：有效 chunks=%d", len(self._chunks))

    @property
    def available(self) -> bool:
        """索引是否可用（语料非空且建索引成功）。"""
        return self._bm25 is not None

    def search(self, query: str, top_k: int) -> list[str]:
        """对 query 检索，返回按 BM25 分数降序的 top_k 个 chunk 文本（score>0 才返回）。"""
        if not self._bm25 or top_k <= 0:
            return []
        query_tokens = tokenize_list(query)
        if not query_tokens:
            return []
        scores = self._bm25.get_scores(query_tokens)  # ndarray[float]，与 self._chunks 对齐
        # 按分数降序取 top_k；分数 <= 0 的不返回（没有命中词）
        ranked = sorted(
            ((float(scores[i]), i) for i in range(len(self._chunks))),
            key=lambda x: x[0],
            reverse=True,
        )
        out: list[str] = []
        for score, i in ranked:
            if score <= 0:
                break
            out.append(self._chunks[i])
            if len(out) >= top_k:
                break
        return out
