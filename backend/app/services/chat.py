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


def _bm25_score(
    query_tokens: list[str],
    doc_tokens: list[str],
    doc_freqs: dict[str, int],
    total_docs: int,
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """计算单篇文档的 BM25 分数。

    BM25 公式：score = Σ IDF(qi) × (tf(qi,D) × (k1+1)) / (tf(qi,D) + k1 × (1-b+b×|D|/avgdl))

    Args:
        query_tokens: 查询的 token 列表。
        doc_tokens: 文档的 token 列表。
        doc_freqs: 各 token 在整个语料库中出现的文档数 {token: count}。
        total_docs: 语料库总文档数。
        avg_doc_len: 语料库平均文档长度（token 数）。
        k1: 词频饱和参数，控制 TF 的影响上限（默认 1.5）。
        b: 长度归一化参数，0=不考虑长度，1=完全归一化（默认 0.75）。

    Returns:
        float: BM25 分数。
    """
    import math

    if not doc_tokens or not query_tokens:
        return 0.0

    doc_len = len(doc_tokens)
    # 统计文档中每个 token 的词频
    tf_map: dict[str, int] = {}
    for t in doc_tokens:
        tf_map[t] = tf_map.get(t, 0) + 1

    score = 0.0
    for qt in query_tokens:
        tf = tf_map.get(qt, 0)
        if tf == 0:
            continue
        # IDF = ln((N - df + 0.5) / (df + 0.5) + 1)
        df = doc_freqs.get(qt, 0)
        idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1.0)
        # TF 饱和项：(tf × (k1+1)) / (tf + k1 × (1 - b + b × doc_len/avgdl))
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * doc_len / max(avg_doc_len, 1))
        score += idf * numerator / denominator

    return score


def retrieve_contexts_bm25(question: str, chunks: list[str], top_k: int) -> list[str]:
    """使用 BM25 算法从论文切块中召回最相关的上下文。

    相比旧版 retrieve_contexts_lexical 的改进：
    ✅ 词频（TF）：同一个词出现多次权重更高（不再是 0/1）
    ✅ 逆文档频率（IDF）：稀有词权重更高，常见词权重降低
    ✅ 长度归一化：长文档不会因为词多就得分更高
    ✅ 饱和函数：词频不会无限增长，有上限
    ✅ 可调参数：k1（词频饱和）、b（长度归一化）
    ✅ 中文支持：字符二元组 bigram 分词

    Args:
        question: 用户问题。
        chunks: 论文切块列表。
        top_k: 返回最相关的 top_k 个切块。

    Returns:
        list[str]: 按相关性降序排列的切块列表。
    """
    if not chunks:
        return []

    from .token_utils import tokenize_list

    # 1. 对所有切块分词
    query_tokens = tokenize_list(question)
    if not query_tokens:
        return chunks[:top_k]

    doc_token_lists = [tokenize_list(chunk) for chunk in chunks]

    # 2. 计算每个 token 在多少篇文档中出现过（DF）
    doc_freqs: dict[str, int] = {}
    for doc_tokens in doc_token_lists:
        seen = set(doc_tokens)
        for t in seen:
            doc_freqs[t] = doc_freqs.get(t, 0) + 1

    # 3. 计算平均文档长度
    total_docs = len(chunks)
    total_tokens = sum(len(dt) for dt in doc_token_lists)
    avg_doc_len = total_tokens / max(total_docs, 1)

    # 4. 对每个切块计算 BM25 分数
    scored: list[tuple[float, str]] = []
    for chunk, doc_tokens in zip(chunks, doc_token_lists, strict=False):
        score = _bm25_score(query_tokens, doc_tokens, doc_freqs, total_docs, avg_doc_len)
        scored.append((score, chunk))

    # 5. 按分数降序排列，取 top_k（分数 > 0 的才返回）
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [text for score, text in scored[:top_k] if score > 0]
    if selected:
        return selected
    # 全部得分为 0 时，返回原始顺序的 top_k
    return chunks[:top_k]


# 旧版保留兼容（已被 retrieve_contexts_bm25 替代）
def retrieve_contexts_lexical(question: str, chunks: list[str], top_k: int) -> list[str]:
    """词法重叠召回（已弃用，内部重定向到 BM25）。"""
    return retrieve_contexts_bm25(question, chunks, top_k)


def _exclude_reference_chunks(
    chunks: list[str],
    paper_id: str,
    storage,
) -> tuple[list[str], list[dict]]:
    """按 chunks_meta.json 过滤参考文献块；无元数据的旧数据原样返回。"""
    try:
        metas = storage.load_chunk_metas(paper_id)
    except Exception:
        metas = []
    if not metas or len(metas) != len(chunks):
        return chunks, []
    kept = [(chunk, meta) for chunk, meta in zip(chunks, metas, strict=False) if not meta.get("is_reference")]
    if not kept:  # 全是参考文献（如用户明确问参考文献）则不过滤
        return chunks, metas
    return [chunk for chunk, _ in kept], [meta for _, meta in kept]


def retrieve_contexts(question: str, paper_id: str, top_k: int, storage) -> list[str]:
    """多路召回：向量检索 + BM25 词法召回，去重融合。

    召回策略：
    1. 向量语义检索（ChromaDB），取 top_k 条
    2. BM25 词法检索，取 top_k 条
    3. 融合去重：向量结果优先（语义更准），BM25 补充（词汇精确匹配）
    4. 如果向量检索失败，回退为纯 BM25

    Args:
        question: 用户问题。
        paper_id: 论文 ID。
        top_k: 每路召回的最大数量。
        storage: 文件存储实例。

    Returns:
        list[str]: 融合后的上下文列表。
    """
    # 向量语义检索（向量库侧已默认排除 element_type=reference）
    vector_contexts = storage.search_similar_chunks(paper_id=paper_id, question=question, top_k=top_k)

    # BM25 词法检索：参考文献块默认排除出 RAG 上下文
    chunks = storage.load_chunks(paper_id)
    chunks, _ = _exclude_reference_chunks(chunks, paper_id, storage)
    bm25_contexts = retrieve_contexts_bm25(question, chunks, top_k) if chunks else []

    if not vector_contexts and not bm25_contexts:
        return []

    # 向量检索失败，纯 BM25 回退
    if not vector_contexts:
        return bm25_contexts

    # BM25 失败（chunks 为空），纯向量结果
    if not bm25_contexts:
        return vector_contexts[:top_k]

    # 多路融合：向量优先 + BM25 补充，去重
    seen: set[int] = set()
    merged: list[str] = []

    # 向量结果优先（语义相似度更高）
    for ctx in vector_contexts:
        h = hash(ctx)
        if h not in seen:
            seen.add(h)
            merged.append(ctx)

    # BM25 补充（捕获向量可能漏掉的精确词汇匹配）
    for ctx in bm25_contexts:
        h = hash(ctx)
        if h not in seen:
            seen.add(h)
            merged.append(ctx)

    return merged[:top_k]


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
        chunks = storage.load_chunks(pid)
        if not chunks:
            continue
        # 参考文献块默认排除出深度检索上下文（与单论文 RAG 行为一致）
        chunks, _ = _exclude_reference_chunks(chunks, pid, storage)
        if not chunks:
            continue
        bm25_hits = retrieve_contexts_bm25(question, chunks, bm25_top_k)
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
