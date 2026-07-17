import json
import logging
from typing import Any

from ..config import get_settings
from .hitl_coordinator import HitlCoordinator, get_hitl_coordinator

settings = get_settings()
logger = logging.getLogger("chat")


def get_deep_search_hitl() -> HitlCoordinator:
    """Deep Search HITL coordinator (StreamBus + store + waiters)."""
    return get_hitl_coordinator()


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文约 1.5 token/字，英文约 0.75 token/word）。"""
    if not text:
        return 0
    cn_chars = sum(1 for c in text if '一' <= c <= '鿿')
    other_len = len(text) - cn_chars
    return int(cn_chars * 1.5 + other_len * 0.4)


def _bm25_search(
    question: str,
    paper_id: str,
    top_k: int,
    storage,
) -> list[str]:
    """用该论文的 BM25 倒排索引检索（命中倒排表的文档才打分，O(命中数)）。

    索引由 ``storage.get_bm25_index`` 懒建并缓存（论文重新入库时失效）。
    无 chunks 或无命中词时返回空列表。
    """
    idx = storage.get_bm25_index(paper_id)
    if idx is None:
        return []
    return idx.search(question, top_k)


def retrieve_contexts_bm25(question: str, chunks: list[str], top_k: int) -> list[str]:
    """对给定 chunks 做 BM25 检索（兼容旧签名；内部走 rank_bm25 的 BM25Okapi）。

    注意：每次调用都重建一次索引，适合一次性/跨论文场景；单论文高频查询应改用
    ``storage.get_bm25_index(paper_id)`` 拿缓存的索引。
    """
    if not chunks:
        return []
    from .bm25_index import BM25Index

    return BM25Index(chunks).search(question, top_k)


# 旧版词法召回入口（已被 retrieve_contexts_bm25 替代）
def retrieve_contexts_lexical(question: str, chunks: list[str], top_k: int) -> list[str]:
    """词法重叠召回（已弃用，内部重定向到 BM25）。"""
    return retrieve_contexts_bm25(question, chunks, top_k)


def _rrf_fuse(ranked_lists: list[list[str]], top_k: int, rrf_k: int) -> list[str]:
    """标准 RRF（Reciprocal Rank Fusion）融合多路召回结果。

    公式：score(doc) = Σ_i  1 / (rrf_k + rank_i(doc))   （rank_i 为该路中的 1-indexed 排名）
    某路未命中的文档不贡献分数。rrf_k 越大，排名差异的影响越平缓（标准取 60）。
    返回按 RRF 分数降序的 top_k 个 chunk 文本（去重）。
    """
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (rrf_k + rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [item for item, _ in ranked[:top_k]]


def retrieve_contexts(question: str, paper_id: str, top_k: int, storage) -> list[str]:
    """混合检索：向量语义召回 + BM25 词法召回，用 **标准 RRF** 融合后取 top_k。

    流程：
    1. 两路并行召回，每路过采 ``rag_rrf_candidate_k`` 条（融合前留足候选）；
       - 向量路：``storage.search_similar_chunks``（ChromaDB，按语义相似度降序）
       - BM25 路：``_bm25_search``（rank_bm25 倒排索引，按词法相关度降序）
    2. RRF 融合两路排名 → 统一排序；
    3. 取 top_k 返回。

    RRF 的好处：两路都命中且排名靠前的文档得分最高；任一路空自动退化为该路结果
    （等价于旧的"向量失败回退 BM25 / BM25 失败回退向量"逻辑，但更平滑）。
    """
    candidate_k = max(top_k, settings.rag_rrf_candidate_k)

    # 1. 向量语义检索（ChromaDB）
    vector_contexts = storage.search_similar_chunks(
        paper_id=paper_id, question=question, top_k=candidate_k
    )

    # 2. BM25 词法检索（倒排索引，命中词才打分）
    bm25_contexts = _bm25_search(question, paper_id, candidate_k, storage)

    # 3. RRF 融合（两路都空时自然返回空列表）
    return _rrf_fuse([vector_contexts, bm25_contexts], top_k, settings.rag_rrf_k)


def retrieve_global_contexts(
    question: str,
    top_k: int,
    storage,
    settings=None,
) -> list[str]:
    """两阶段跨论文检索（深度研究专用）。

    初排（Stage 1）：
        - 向量语义检索：跨全库搜索（ChromaDB 不带 paper_id 过滤），取 top-N
        - BM25 词法检索：遍历每篇论文各取 top-N
        - 两路融合去重

    精排（Stage 2）：
        - CrossEncoder 对融合结果重新打分，保留 top_k 条
        - 精排器不可用时直接返回初排结果

    Args:
        question: 用户问题。
        top_k: 精排后最终保留的数量。
        storage: 文件存储实例。
        settings: 配置实例。

    Returns:
        list[str]: 排序后的上下文文本列表。
    """
    import time

    from .reranker import get_reranker

    effective_settings = settings or get_settings()
    t_start = time.time()
    logger.info("[GLOBAL_RETRIEVAL] ========== 两阶段检索开始 ==========")
    logger.info(
        "[GLOBAL_RETRIEVAL] query=%s  initial_top_k=%d  bm25_top_k=%d  reranker_top_k=%d",
        question[:80],
        effective_settings.global_retrieval_top_k,
        effective_settings.global_retrieval_bm25_top_k,
        effective_settings.reranker_top_k,
    )

    # ---- 初排 (Stage 1) ----
    initial_top_k = effective_settings.global_retrieval_top_k
    bm25_top_k = effective_settings.global_retrieval_bm25_top_k

    # 1a. 跨全库向量检索
    logger.info("[GLOBAL_RETRIEVAL] Stage1-向量: 跨全库检索 top-%d ...", initial_top_k)
    vector_results = storage.search_global_chunks(
        question=question,
        top_k=initial_top_k,
    )
    # 统计向量结果来自几篇论文
    vector_paper_ids = set(r.get("paper_id", "?") for r in vector_results)
    logger.info(
        "[GLOBAL_RETRIEVAL] Stage1-向量: 命中 %d 条, 涉及 %d 篇论文 %s",
        len(vector_results),
        len(vector_paper_ids),
        vector_paper_ids,
    )

    # 1b. 跨全库 BM25：逐篇论文检索
    #     不依赖 papers.json（可能为空），直接扫描 processed 目录发现有 chunks 的论文
    all_bm25_chunks: list[dict] = []
    processed_dir = storage.processed_dir
    paper_ids_on_disk: list[str] = sorted(
        d.name for d in processed_dir.iterdir() if d.is_dir() and (d / "chunks.json").exists()
    )
    logger.info(
        "[GLOBAL_RETRIEVAL] Stage1-BM25: 扫描到 %d 篇有 chunks 的论文 %s",
        len(paper_ids_on_disk),
        paper_ids_on_disk,
    )
    for pid in paper_ids_on_disk:
        # 用 storage 缓存的 BM25 索引（首次建后复用，避免每篇论文每次查询都重建倒排表）
        idx = storage.get_bm25_index(pid)
        if idx is None:
            continue
        bm25_hits = idx.search(question, bm25_top_k)
        for hit in bm25_hits:
            all_bm25_chunks.append(
                {
                    "text": hit,
                    "paper_id": pid,
                    "source": "bm25",
                }
            )
    bm25_paper_ids = set(r.get("paper_id", "?") for r in all_bm25_chunks)
    logger.info(
        "[GLOBAL_RETRIEVAL] Stage1-BM25: 命中 %d 条, 涉及 %d 篇论文 %s",
        len(all_bm25_chunks),
        len(bm25_paper_ids),
        bm25_paper_ids,
    )

    # 1c. 融合去重（向量结果优先）
    seen_texts: set[int] = set()
    merged: list[dict] = []

    for item in vector_results:
        h = hash(item["text"])
        if h not in seen_texts:
            seen_texts.add(h)
            item["source"] = "vector"
            merged.append(item)

    for item in all_bm25_chunks:
        h = hash(item["text"])
        if h not in seen_texts:
            seen_texts.add(h)
            merged.append(item)

    vector_only = sum(1 for m in merged if m.get("source") == "vector")
    bm25_only = sum(1 for m in merged if m.get("source") == "bm25")
    logger.info(
        "[GLOBAL_RETRIEVAL] Stage1-融合: 去重后 %d 条 (向量 %d + BM25补充 %d)",
        len(merged),
        vector_only,
        bm25_only,
    )

    if not merged:
        logger.warning("[GLOBAL_RETRIEVAL] Stage1 结果为空，返回 []")
        return []

    # ---- 精排 (Stage 2) ----
    if effective_settings.reranker_enabled:
        logger.info(
            "[GLOBAL_RETRIEVAL] Stage2-精排: CrossEncoder(%s) 开始 (%d → %d) ...",
            effective_settings.reranker_model_name,
            len(merged),
            top_k,
        )
        t_rerank_start = time.time()
        reranker = get_reranker(
            model_name=effective_settings.reranker_model_name,
            max_length=effective_settings.reranker_max_length,
        )
        reranked = reranker.rerank(
            query=question,
            candidates=merged,
            top_k=top_k,
        )
        t_rerank_cost = time.time() - t_rerank_start
        logger.info(
            "[GLOBAL_RETRIEVAL] Stage2-精排: 完成, 耗时 %.2fs, %d → %d 条",
            t_rerank_cost,
            len(merged),
            len(reranked),
        )
        # 打印精排后每个结果的来源论文和分数
        for i, item in enumerate(reranked[:5]):
            logger.info(
                "  [#%d] paper=%s  score=%.4f  source=%s  text=%.80s...",
                i + 1,
                item.get("paper_id", "?"),
                item.get("reranker_score", 0),
                item.get("source", "?"),
                item["text"][:80],
            )
        if len(reranked) > 5:
            logger.info("  ... 共 %d 条（仅显示前 5 条）", len(reranked))

        t_total = time.time() - t_start
        logger.info(
            "[GLOBAL_RETRIEVAL] ========== 两阶段检索完成, 总耗时 %.2fs ==========",
            t_total,
        )
        return [item["text"] for item in reranked]

    # 精排关闭，直接返回初排结果
    logger.info(
        "[GLOBAL_RETRIEVAL] Stage2-精排: 已禁用 (reranker_enabled=False), 返回初排前 %d 条",
        top_k,
    )
    t_total = time.time() - t_start
    logger.info(
        "[GLOBAL_RETRIEVAL] ========== 检索完成（仅初排）, 总耗时 %.2fs ==========",
        t_total,
    )
    return [item["text"] for item in merged[:top_k]]


def _sse_event(data: dict[str, Any]) -> str:
    """格式化 SSE 事件。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def chat_with_paper_stream(
    paper_id,
    payload,
    storage,
    summary_template="tinghua.md",
    settings=None,
    user_id=None,
    history=None,
):
    """流式版 chat_with_paper，支持普通模式和深度研究模式。"""
    import uuid

    from ..agent.bootstrap import get_runtime
    from .agent_chat import agent_chat_stream
    from .deep_search_hitl import stream_deep_search

    effective_settings = settings or get_settings()
    logger.info(
        "[CHAT_STREAM] user_id=%s paper_id=%s deep_search=%s question=%s",
        user_id,
        paper_id,
        payload.deep_search,
        payload.question[:80],
    )
    top_k = payload.top_k or max(8, effective_settings.rag_default_top_k)

    if payload.deep_search:
        contexts = retrieve_global_contexts(
            question=payload.question,
            top_k=top_k,
            storage=storage,
            settings=effective_settings,
        )
        full_summary = storage.read_result(paper_id, "summary", summary_template=summary_template)
        if full_summary:
            contexts.insert(0, f"[当前论文摘要]\n{full_summary}")
        logger.info(
            "[CHAT_STREAM] 深度研究上下文就绪: %d 条 (含摘要 %s)",
            len(contexts),
            "有" if full_summary else "无",
        )
    else:
        contexts = retrieve_contexts(payload.question, paper_id=paper_id, top_k=top_k, storage=storage)
        full_summary = storage.read_result(paper_id, "summary", summary_template=summary_template)
        if full_summary:
            contexts.insert(0, f"[全局摘要]\n{full_summary}")

    if not contexts:
        contexts = ["暂无可用上下文。"]

    if payload.deep_search:
        hitl_coord = get_deep_search_hitl() if effective_settings.hitl_deep_search_enabled else None
        chat_sid = payload.session_id or f"deep-{uuid.uuid4().hex[:12]}"
        agent_sid = f"run-{uuid.uuid4().hex[:12]}"
        try:
            get_runtime()
            async for event in stream_deep_search(
                question=payload.question,
                contexts=contexts,
                settings=effective_settings,
                user_id=user_id,
                paper_id=paper_id,
                history=history,
                hitl_coordinator=hitl_coord,
                chat_session_id=chat_sid,
                agent_session_id=agent_sid,
            ):
                yield event
        except RuntimeError as exc:
            yield _sse_event({"type": "error", "text": f"Agent runtime unavailable: {exc}"})
        return

    if not (getattr(effective_settings, "agent_native_chat_enabled", True) and effective_settings.agent_enable_tools):
        yield _sse_event({"type": "error", "text": "Agent chat is disabled in settings."})
        return

    session_id = f"chat-{uuid.uuid4().hex[:12]}"
    try:
        get_runtime()
        async for event in agent_chat_stream(
            session_id=session_id,
            question=payload.question,
            contexts=contexts,
            settings=effective_settings,
            user_id=user_id,
            paper_id=paper_id,
        ):
            yield event
    except RuntimeError as exc:
        yield _sse_event({"type": "error", "text": f"Agent runtime unavailable: {exc}"})
