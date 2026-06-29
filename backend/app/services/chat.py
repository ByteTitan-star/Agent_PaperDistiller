import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..harness.events import EventBus
from ..harness.hitl._types import HITLDecision
from ..harness.hitl.base import HITLManager
from ..harness.hitl.store import HITLStore
from ..schemas import ChatRequest, ChatResponse
from .token_utils import tokenize

settings = get_settings()
logger = logging.getLogger("chat")


# ---------------------------------------------------------------------------
# Deep Search 专用 HITL 管理器（独立于 pipeline 的 HITL）
# ---------------------------------------------------------------------------
_deep_search_hitl_manager: HITLManager | None = None


def get_deep_search_hitl() -> HITLManager:
    """返回 Deep Search 专用的 HITLManager 单例。

    与 pipeline 的 HITLManager 完全隔离：
    - 使用独立的存储目录 data/hitl_deep_search/
    - 不使用 checkpoints 配置机制
    - 由 deep_search_stream() 直接调用 interrupt/wait_for_decision
    """
    global _deep_search_hitl_manager
    if _deep_search_hitl_manager is None:
        backend_root = Path(__file__).resolve().parents[1]
        store = HITLStore(data_dir=backend_root / "data" / "hitl_deep_search")
        event_bus = EventBus()
        _deep_search_hitl_manager = HITLManager(event_bus=event_bus, store=store, checkpoints=[])
    return _deep_search_hitl_manager


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
    for chunk, doc_tokens in zip(chunks, doc_token_lists):
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
    # 向量语义检索
    vector_contexts = storage.search_similar_chunks(paper_id=paper_id, question=question, top_k=top_k)

    # BM25 词法检索
    chunks = storage.load_chunks(paper_id)
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
    logger.info(
        "[GLOBAL_RETRIEVAL] ========== 两阶段检索开始 =========="
    )
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
        question=question, top_k=initial_top_k,
    )
    # 统计向量结果来自几篇论文
    vector_paper_ids = set(r.get("paper_id", "?") for r in vector_results)
    logger.info(
        "[GLOBAL_RETRIEVAL] Stage1-向量: 命中 %d 条, 涉及 %d 篇论文 %s",
        len(vector_results), len(vector_paper_ids), vector_paper_ids,
    )

    # 1b. 跨全库 BM25：逐篇论文检索
    #     不依赖 papers.json（可能为空），直接扫描 processed 目录发现有 chunks 的论文
    all_bm25_chunks: list[dict] = []
    processed_dir = storage.processed_dir
    paper_ids_on_disk: list[str] = sorted(
        d.name for d in processed_dir.iterdir()
        if d.is_dir() and (d / "chunks.json").exists()
    )
    logger.info("[GLOBAL_RETRIEVAL] Stage1-BM25: 扫描到 %d 篇有 chunks 的论文 %s", len(paper_ids_on_disk), paper_ids_on_disk)
    for pid in paper_ids_on_disk:
        chunks = storage.load_chunks(pid)
        if not chunks:
            continue
        bm25_hits = retrieve_contexts_bm25(question, chunks, bm25_top_k)
        for hit in bm25_hits:
            all_bm25_chunks.append({
                "text": hit,
                "paper_id": pid,
                "source": "bm25",
            })
    bm25_paper_ids = set(r.get("paper_id", "?") for r in all_bm25_chunks)
    logger.info(
        "[GLOBAL_RETRIEVAL] Stage1-BM25: 命中 %d 条, 涉及 %d 篇论文 %s",
        len(all_bm25_chunks), len(bm25_paper_ids), bm25_paper_ids,
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
        len(merged), vector_only, bm25_only,
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
            t_rerank_cost, len(merged), len(reranked),
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

def build_fallback_answer(question: str, contexts: list[str], reason: str | None = None) -> str:
    # 如果有可用上下文，构建一个基于上下文的回答
    if contexts and contexts[0] != "暂无可用上下文。":
        lines = [
            f"关于「{question}」的回答：",
            "",
            "以下信息来自当前论文的检索结果，供参考：",
        ]
        for idx, context in enumerate(contexts[:3], start=1):
            lines.append(f"{idx}. {context[:300]}")
        return "\n".join(lines)
    return f"关于「{question}」：抱歉，当前无法获取足够信息来回答这个问题。请确认论文已完成解析，或尝试换个问法。"

def build_deepseek_messages(question: str, contexts: list[str], history: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    import datetime
    context_block = "\n\n".join(
        f"[Context {idx}]\n{context[:2500]}" for idx, context in enumerate(contexts, start=1)
    )
    today = datetime.date.today().strftime("%Y年%m月%d日")
    system_prompt = (
        f"你是「PaperDistiller 智能知识助手」，一个多功能 AI 助手。\n"
        f"当前日期：{today}。当用户提到「今天」、「最近」等时间相关表述时，以这个日期为准。\n"
        f"用户当前正在阅读一篇论文，下方提供了该论文的摘要与正文片段作为参考。\n\n"
        f"【核心规则】：\n"
        f"- 当你拥有可用工具（如 web_search）时，遇到任何需要实时信息、外部数据、你不确定的问题，**必须调用工具搜索**，绝不要说「我无法获取」「我没有联网能力」。\n"
        f"- 天气、新闻、最新研究、代码仓库、开源项目等实时信息 → 立即调用 web_search。\n"
        f"- 论文相关问题 → 优先参考论文上下文。\n"
        f"- 通用知识问题 → 直接用你的知识回答。\n"
        f"- 回答使用 Markdown 格式，结构清晰。\n"
        f"- 不要暴露你的底层模型名称。"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    # 插入历史对话（上下文管理：短历史全保留，长历史压缩旧消息）
    if history:
        recent_n = 10  # 最近 N 条完整保留
        if len(history) <= recent_n * 2:
            # 历史不长，全部保留
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        else:
            # 历史较长：旧消息压缩为摘要，最近 N 条完整保留
            old_msgs = history[: -recent_n]
            recent_msgs = history[-recent_n:]
            # 压缩旧消息：提取用户问题列表作为上下文摘要
            summary_parts = ["以下是早期对话的摘要（已压缩）："]
            for i, msg in enumerate(old_msgs):
                if msg["role"] == "user":
                    # 截断过长的问题
                    q = msg["content"][:100]
                    summary_parts.append(f"- 用户问：{q}")
            summary_text = "\n".join(summary_parts)
            messages.append({"role": "system", "content": summary_text})
            # 最近 N 条完整保留
            for msg in recent_msgs:
                messages.append({"role": msg["role"], "content": msg["content"]})
    user_prompt = (
        f"用户问题：{question}\n\n"
        f"以下是当前论文的参考信息（如有需要）：\n"
        f"{context_block}"
    )
    messages.append({"role": "user", "content": user_prompt})
    return messages


def sanitize_agent_output(text: str) -> str:
    """移除内部工具标签，确保只返回自然语言。"""
    if not text:
        return ""
    cleaned = re.sub(r"<｜DSML｜[^>]*>", "", text)
    cleaned = re.sub(r"<\|DSML\|[^>]*>", "", cleaned)
    return cleaned.strip()

def split_answer_and_reasoning(text: str) -> tuple[str, str | None]:
    """agent_output = "我们首先需要计算 15% 的小费。

            <think>
            用户问的是餐厅账单小费计算。账单金额是 $85.50，小费比例 15%。
            计算步骤：
            1. 85.50 × 0.15 = 12.825
            2. 四舍五入到两位小数 = 12.83
            3. 加上原账单 = 85.50 + 12.83 = 98.33
            确认计算无误。
            </think>

            最终答案是 $98.33，其中包括 $85.50 的餐费和 $12.83 的小费。

            # 解析结果：
            # answer = "我们首先需要计算 15% 的小费。\n\n最终答案是 $98.33，其中包括 $85.50 的餐费和 $12.83 的小费。"
            # reasoning = "用户问的是餐厅账单小费计算...（完整推理过程）
    """
    text = sanitize_agent_output(text)
    if "<think>" not in text:
        return text.strip(), None
    think_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL)
    reasoning = "\n\n".join(item.strip() for item in think_blocks if item.strip()) or None
    clean_text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return clean_text, reasoning

def _sse_event(data: dict[str, Any]) -> str:
    """格式化 SSE 事件。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def call_deepseek_chat_stream(
    question: str,
    contexts: list[str],
    paper_id: str,
    user_settings: Any = None,
    user_id: int | None = None,
    token_usage: dict[str, int] | None = None,
    history: list[dict[str, str]] | None = None,
):
    """流式版 DeepSeek 调用，逐 token yield SSE 事件（真异步流式）。
    基础问答模式：根据用户问题和上下文，调用 DeepSeek 模型生成回答。
    使用 AsyncOpenAI 异步迭代流，不阻塞事件循环（v3.0：原为同步 OpenAI 阻塞迭代）。
    token_usage: 可选的可变 dict，执行完毕后写入 {"prompt": N, "completion": N}。
    history: 可选的历史对话列表 [{"role": "user/assistant", "content": "..."}]
    """
    effective_settings = user_settings or settings
    if not effective_settings.deepseek_api_key.strip():
        yield _sse_event({"type": "error", "text": "请先在设置页面配置 DeepSeek API Key。"})
        return

    try:
        from openai import AsyncOpenAI
    except ImportError:
        yield _sse_event({"type": "error", "text": "缺少 openai SDK"})
        return

    # 工具准备：统一走 harness 的 HarnessToolRegistry（带事件追踪/限流），
    # 未启动时回退到裸 SkillRegistry。两者接口一致。
    from ..dependencies import get_tool_executor
    tool_executor = get_tool_executor()

    # 工具准备，通过query向量相似度选择与 query 相关的技能
    # 先通过关键词快速匹配，再通过向量相似度选择
    allow_tools = effective_settings.agent_enable_tools
    selected_skills = (
        tool_executor.select_tools(question, top_k=effective_settings.skill_retrieval_top_k, min_similarity=effective_settings.skill_similarity_threshold)
        if allow_tools
        else []
    )
    if not selected_skills:
        allow_tools = False
    logger.info("[TOOLS_STREAM] user_id=%s query=%s skills=%s allow=%s", user_id, question[:50], [s.tool_name for s in selected_skills], allow_tools)
    tools_schema = tool_executor.build_openai_tools(selected_skills)
    skill_hint = tool_executor.build_skill_hint(selected_skills)

    try:
        client = AsyncOpenAI(
            api_key=effective_settings.deepseek_api_key,
            base_url=effective_settings.deepseek_base_url.rstrip("/"),
            timeout=effective_settings.deepseek_timeout_sec,
        )
        messages = build_deepseek_messages(question, contexts, history)
        if skill_hint:
            messages.insert(1, {"role": "system", "content": skill_hint})

        from ..dependencies import storage as _storage

        paper_chunks = _storage.load_chunks(paper_id)
        tool_context = { # 工具调用上下文
            "paper_id": paper_id, "question": question, "chunks": paper_chunks, # 论文id、问题、文本块
            "vector_search": lambda q, k: _storage.search_similar_chunks(  # 向量检索
                paper_id=paper_id, question=str(q), top_k=max(1, int(k)),
            ),
        }

        full_text = ""
        max_rounds = max(1, effective_settings.agent_max_tool_rounds)
        total_prompt = 0
        total_completion = 0

        for _round in range(max_rounds): # 工具调用最大轮数为4
            request_kwargs: dict[str, Any] = { # 请求参数
                "model": effective_settings.deepseek_model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 1200,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if allow_tools and tools_schema:
                request_kwargs["tools"] = tools_schema # 工具列表
                request_kwargs["tool_choice"] = "auto" # 工具选择策略

            stream = await client.chat.completions.create(**request_kwargs) # 创建异步流式请求
            round_content = "" # 本轮对话内容
            tool_calls_acc: dict[int, dict] = {} # 工具调用记录

            async for chunk in stream:
                # 提取 usage（最后一个 chunk 携带）
                if chunk.usage:
                    total_prompt += chunk.usage.prompt_tokens # 累计提示词token
                    total_completion += chunk.usage.completion_tokens # 累计完成token
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta: # 如果没有delta，则跳过
                    continue
                if delta.content:
                    token = delta.content # 获取token
                    round_content += token # 累计本轮对话内容
                    full_text += token
                    yield _sse_event({"type": "token", "text": token}) # 发送token事件      
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                        if tc.function:
                            if tc.function.name:
                                tool_calls_acc[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc.function.arguments

            # 工具调用 → 执行后继续
            if tool_calls_acc and allow_tools and tools_schema:
                tool_calls_list = list(tool_calls_acc.values())
                messages.append({"role": "assistant", "content": round_content, "tool_calls": [
                    {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls_list
                ]})
                for tc in tool_calls_list:
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    result = tool_executor.execute(tc["name"], args if isinstance(args, dict) else {}, context=tool_context)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result, ensure_ascii=False)})
                    yield _sse_event({"type": "tool", "name": tc["name"], "query": args.get("query", "")})
                continue

            break  # 无工具调用，结束

        clean, _ = split_answer_and_reasoning(full_text)
        # DeepSeek 流式可能不返回 usage，用估算兜底
        if total_prompt + total_completion == 0:
            total_completion = _estimate_tokens(full_text)
            total_prompt = _estimate_tokens(" ".join(m.get("content", "") for m in messages if isinstance(m, dict)))
        if token_usage is not None:
            token_usage["prompt"] = total_prompt
            token_usage["completion"] = total_completion
        yield _sse_event({"type": "done", "answer": clean or full_text})

    except Exception as exc:
        yield _sse_event({"type": "error", "text": str(exc)})


async def chat_with_paper_stream(paper_id, payload, storage, summary_template="tinghua.md", settings=None, user_id=None, history=None):
    """流式版 chat_with_paper，支持普通模式和深度研究模式。"""
    effective_settings = settings or get_settings()
    logger.info("[CHAT_STREAM] user_id=%s paper_id=%s deep_search=%s question=%s", user_id, paper_id, payload.deep_search, payload.question[:80])
    top_k = payload.top_k or max(8, effective_settings.rag_default_top_k)

    if payload.deep_search:
        # 深度研究：跨全库检索 + 精排（初排 top-50 → 精排 top-k）
        contexts = retrieve_global_contexts(
            question=payload.question,
            top_k=top_k,
            storage=storage,
            settings=effective_settings,
        )
        # 追加当前论文摘要作为上下文锚点
        full_summary = storage.read_result(paper_id, "summary", summary_template=summary_template)
        if full_summary:
            contexts.insert(0, f"[当前论文摘要]\n{full_summary}")
        logger.info(
            "[CHAT_STREAM] 深度研究上下文就绪: %d 条 (含摘要 %s)",
            len(contexts),
            "有" if full_summary else "无",
        )
    else:
        # 普通问答：单篇论文检索（不变）
        contexts = retrieve_contexts(payload.question, paper_id=paper_id, top_k=top_k, storage=storage)
        full_summary = storage.read_result(paper_id, "summary", summary_template=summary_template)
        if full_summary:
            contexts.insert(0, f"[全局摘要]\n{full_summary}")

    if not contexts:
        contexts = ["暂无可用上下文。"]

    if payload.deep_search:
        hitl_mgr = get_deep_search_hitl() if effective_settings.hitl_deep_search_enabled else None
        async for event in deep_search_stream(payload.question, contexts, effective_settings, user_id, history, hitl_manager=hitl_mgr):
            yield event
    else:
        token_usage: dict[str, int] = {}
        async for event in call_deepseek_chat_stream(payload.question, contexts, paper_id, effective_settings, user_id, token_usage, history):
            yield event
        # 记录 token 用量
        prompt_t = token_usage.get("prompt", 0)
        compl_t = token_usage.get("completion", 0)
        if prompt_t + compl_t > 0:
            from .token_logger import log_token_to_db
            await log_token_to_db(
                user_id=user_id,
                model_name=getattr(effective_settings, "deepseek_model", "deepseek-chat"),
                prompt_tokens=prompt_t,
                completion_tokens=compl_t,
                action_type="chat",
                detail={"paper_id": paper_id} if paper_id else None,
            )
        await asyncio.sleep(0)


async def deep_search_stream(
    question: str,
    contexts: list[str],
    settings: Any = None,
    user_id: int | None = None,
    history: list[dict[str, str]] | None = None,
    hitl_manager: HITLManager | None = None,
):
    """流式深度研究：带阶段推进、来源卡片、结构化输出。

    当 hitl_manager 不为 None 时，会在搜索前和生成报告前暂停等待人工审批：
    - Checkpoint 1 (pre_search): CLARIFY 之后、搜索之前，用户可修改搜索方向
    - Checkpoint 2 (pre_report): 搜索之后、生成报告之前，用户可追加搜索关键词

    Args:
        question: 用户问题。
        contexts: 论文上下文片段列表。
        settings: 框架配置。
        user_id: 用户 ID。
        history: 历史对话列表。
        hitl_manager: Deep Search 专用 HITL 管理器，None 则无中断。
    """
    import logging
    logger = logging.getLogger(__name__)
    from .deep_search_logger import log_separator, log_research_plan, log_hitl_checkpoint, log_error

    settings = settings or get_settings()
    collected_sources = []

    # 写入分隔线，标记新的深度搜索请求
    log_separator()

    # 将历史对话注入上下文，让深度搜索也能感知之前的对话
    agent_contexts = list(contexts)
    if history:
        hist_lines = ["【之前的对话历史】"]
        for msg in history[-10:]:
            role = "用户" if msg["role"] == "user" else "助手"
            hist_lines.append(f"{role}：{msg['content'][:200]}")
        agent_contexts.insert(0, "\n".join(hist_lines))

    # Phase 1: 规划
    yield _sse_event({"type": "phase", "phase": "planning", "label": "正在分析问题，制定研究计划..."})

    # Phase 2: 生成研究计划（HITL 启用时用 LLM 生成详细计划，否则用简单澄清）
    clarification = None
    research_plan = None

    if hitl_manager is not None:
        # HITL 模式：生成结构化研究计划供用户确认。
        # supervisor_planning_enabled=True 时用多智能体 Supervisor 协作规划，否则单次 LLM 规划。
        if getattr(settings, "supervisor_planning_enabled", False):
            research_plan = await _generate_research_plan_via_supervisor(question, contexts, settings)
            if research_plan is None:
                research_plan = await _generate_research_plan(question, contexts, settings)
        else:
            research_plan = await _generate_research_plan(question, contexts, settings)
        log_research_plan(research_plan, question)
        if research_plan:
            yield _sse_event({"type": "phase", "phase": "planning", "label": "研究计划已生成，等待确认..."})
    else:
        # 非 HITL 模式：保持原有简单澄清逻辑
        if settings.react_enable_clarification and len(question) > 15:
            clarification = await _try_clarify(question, contexts, settings)
            if clarification:
                yield _sse_event({"type": "phase", "phase": "clarifying", "label": "需要确认研究方向", "detail": clarification})

    # ── HITL Checkpoint 1: 展示研究计划，让用户确认或修改 ──
    if hitl_manager is not None:
        # 构建展示给用户的研究分析
        plan_display = {}
        if research_plan:
            plan_display = {
                "understanding": research_plan.get("understanding", []),
                "search_plan": research_plan.get("search_plan", []),
                "focus_areas": research_plan.get("focus_areas", []),
                "estimated_depth": research_plan.get("estimated_depth", ""),
            }
        else:
            # LLM 生成计划失败时的降级展示
            plan_display = {
                "understanding": [f"分析问题：{question}"],
                "search_plan": [{"step": 1, "action": "搜索相关问题", "keywords": question}],
                "focus_areas": [],
                "estimated_depth": "一般分析",
            }

        hitl_state = await hitl_manager.interrupt("deep_search_pre_search", {
            "question": question,
            "research_plan": plan_display,
            "phase": "pre_search",
        })
        yield _sse_event({
            "type": "hitl_approval",
            "hitl_id": hitl_state.id,
            "checkpoint": "pre_search",
            "title": "📋 研究计划确认",
            "message": "我分析了你的问题，以下是我的理解和搜索计划，请确认或调整：",
            "current_state": {
                "question": question,
                "research_plan": plan_display,
            },
        })
        # 等待用户决策，期间发送 keepalive 防止 SSE 连接超时
        decision_task = asyncio.create_task(
            hitl_manager.wait_for_decision(hitl_state.id, timeout=3600.0)
        )
        try:
            while not decision_task.done():
                yield ": hitl_keepalive\n\n"
                await asyncio.sleep(10)
            decision = decision_task.result()
        except asyncio.TimeoutError:
            yield _sse_event({"type": "error", "text": "等待用户确认超时（1小时），深度搜索已取消。"})
            return
        except Exception as exc:
            yield _sse_event({"type": "error", "text": f"HITL 等待异常：{exc}"})
            return
        if decision.action == "rejected":
            log_hitl_checkpoint("pre_search", "rejected")
            yield _sse_event({"type": "done", "answer": "用户取消了深度搜索。"})
            return
        if decision.action == "edited" and decision.edited_state:
            # 用户修改了搜索问题或追加关键词
            edited_question = decision.edited_state.get("question", "").strip()
            extra_keywords = decision.edited_state.get("extra_keywords", "").strip()
            if edited_question:
                question = edited_question
            if extra_keywords:
                clarification = f"用户补充要求：{extra_keywords}"
            log_hitl_checkpoint("pre_search", "edited", decision.edited_state)
        else:
            log_hitl_checkpoint("pre_search", "approved")

    # Phase 3: 检索
    yield _sse_event({"type": "phase", "phase": "searching", "label": "正在检索相关资源..."})

    from ..harness.react.langgraph_agent import run_react_search
    try:
        answer, thinking_chain, prompt_tokens, completion_tokens, sources = await run_react_search(
            question=question,
            paper_context=agent_contexts,
            settings=settings,
            clarify_hint=clarification,
        )
        # 记录 token 用量（async 上下文直接 await）
        if prompt_tokens + completion_tokens > 0:
            from .token_logger import log_token_to_db
            await log_token_to_db(
                user_id=user_id,
                model_name=getattr(settings, "deepseek_model", "deepseek-chat"),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                action_type="deep_search",
            )
        # 动态推送推理过程（搜索工具调用 + 来源卡片）
        for step in thinking_chain:
            if "搜索" in step and "：" in step:
                yield _sse_event({"type": "tool", "name": "web_search", "query": step.split("：")[-1]})
            elif "找到" in step or "相关结果" in step:
                yield _sse_event({"type": "source", "snippet": step})
            await asyncio.sleep(0.05)
        # 推送来源链接卡片
        for s in sources:
            yield _sse_event({"type": "source", "title": s["title"], "url": s["url"]})
            await asyncio.sleep(0.05)
    except Exception as exc:
        logger.error("Deep search failed: %s", exc, exc_info=True)
        answer = f"深度搜索遇到问题：{exc}\n\n以下是基于论文内容的基础回答：\n\n{build_fallback_answer(question, contexts)}"
        thinking_chain = [f"深度搜索遇到问题：{exc}"]
        sources = []

    # ── HITL Checkpoint 2: 搜索后审核结果 ──
    if hitl_manager is not None and sources:
        hitl_state = await hitl_manager.interrupt("deep_search_pre_report", {
            "question": question,
            "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
            "answer_preview": answer[:500],
            "phase": "pre_report",
        })
        yield _sse_event({
            "type": "hitl_approval",
            "hitl_id": hitl_state.id,
            "checkpoint": "pre_report",
            "title": "审核搜索结果",
            "message": f"搜索完成，找到 {len(sources)} 个来源。可以批准生成报告、取消、或追加搜索关键词。",
            "current_state": {
                "question": question,
                "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
                "answer_preview": answer[:500],
            },
        })
        # 等待用户决策
        decision_task = asyncio.create_task(
            hitl_manager.wait_for_decision(hitl_state.id, timeout=3600.0)
        )
        try:
            while not decision_task.done():
                yield ": hitl_keepalive\n\n"
                await asyncio.sleep(10)
            decision = decision_task.result()
        except asyncio.TimeoutError:
            yield _sse_event({"type": "error", "text": "等待用户确认超时（1小时），深度搜索已取消。"})
            return
        except Exception as exc:
            yield _sse_event({"type": "error", "text": f"HITL 等待异常：{exc}"})
            return
        if decision.action == "rejected":
            log_hitl_checkpoint("pre_report", "rejected")
            yield _sse_event({"type": "done", "answer": "用户取消了报告生成。"})
            return
        if decision.action == "edited" and decision.edited_state:
            extra_keywords = (decision.edited_state.get("extra_keywords") or "").strip()
            log_hitl_checkpoint("pre_report", "edited", {"extra_keywords": extra_keywords})
            if extra_keywords:
                # 追加搜索
                yield _sse_event({"type": "phase", "phase": "searching", "label": f"追加搜索：{extra_keywords}..."})
                try:
                    extra_answer, extra_thinking, ep_tok, ec_tok, extra_sources = await run_react_search(
                        question=f"{question} {extra_keywords}",
                        paper_context=agent_contexts,
                        settings=settings,
                        clarify_hint=clarification,
                    )
                    answer += f"\n\n---\n### 补充搜索结果（关键词：{extra_keywords}）\n\n{extra_answer}"
                    sources.extend(extra_sources)
                    thinking_chain = thinking_chain or []
                    thinking_chain.extend(extra_thinking)
                    for s in extra_sources:
                        yield _sse_event({"type": "source", "title": s["title"], "url": s["url"]})
                        await asyncio.sleep(0.05)
                except Exception as exc:
                    logger.warning("Extra search failed: %s", exc)
        else:
            log_hitl_checkpoint("pre_report", "approved")

    # Phase 4: 生成报告
    yield _sse_event({"type": "phase", "phase": "generating", "label": "正在生成研究报告..."})

    # 流式输出最终答案
    for i in range(0, len(answer), 3):
        chunk = answer[i:i+3]
        yield _sse_event({"type": "token", "text": chunk})
        await asyncio.sleep(0.01)

    yield _sse_event({
        "type": "done",
        "answer": answer,
        "thinking_chain": thinking_chain,
        "sources": collected_sources,
    })


async def _try_clarify(question: str, contexts: list[str], settings: Any) -> str | None:
    """纯 LLM 判断是否需要澄清，不阻塞。返回澄清提示文本或 None。"""
    from ..harness.react.prompts import CLARIFY_SYSTEM, CLARIFY_USER

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        context_summary = "\n".join(contexts[:3])[:2000]
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": CLARIFY_SYSTEM},
                {"role": "user", "content": CLARIFY_USER.format(
                    context=context_summary, question=question,
                )},
            ],
            max_tokens=300,
            temperature=0.1,
        )
        answer = (resp.choices[0].message.content or "").strip()
        if answer.upper() == "NO" or len(answer) < 10:
            logger.info("[CLARIFY] 用户问题: %s → 判定: 无需澄清 (NO)", question[:80])
            return None
        logger.info("[CLARIFY] 用户问题: %s → 判定: 需要澄清 (YES) → 澄清内容: %s", question[:80], answer)
        return answer
    except Exception:
        logger.warning("[CLARIFY] 澄清判断失败，跳过", exc_info=True)
        return None


async def _generate_research_plan(question: str, contexts: list[str], settings: Any) -> dict | None:
    """LLM 生成结构化研究计划（用于 HITL Checkpoint 1 展示给用户确认）。

    返回 dict 格式：
        {
            "understanding": ["需求1", "需求2"],
            "search_plan": [{"step": 1, "action": "...", "keywords": "..."}],
            "focus_areas": ["领域1", "领域2"],
            "estimated_depth": "一般分析"
        }
    解析失败时返回 None，不影响后续流程。
    """
    from ..harness.react.prompts import RESEARCH_PLAN_SYSTEM, RESEARCH_PLAN_USER

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        context_summary = "\n".join(contexts[:3])[:2000]
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": RESEARCH_PLAN_SYSTEM},
                {"role": "user", "content": RESEARCH_PLAN_USER.format(
                    context=context_summary, question=question,
                )},
            ],
            max_tokens=600,
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # 提取 JSON（兼容 ```json ... ``` 包裹）
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        import json
        plan = json.loads(raw.strip())
        # 基本校验
        if "understanding" in plan and "search_plan" in plan:
            return plan
        return None
    except Exception:
        logger.warning("Research plan generation failed, skipping", exc_info=True)
        return None


async def _generate_research_plan_via_supervisor(question: str, contexts: list[str], settings: Any) -> dict | None:
    """用 SupervisorPattern 多智能体协作生成结构化研究计划（开关 supervisor_planning_enabled）。

    流程：DeepSeek（主管）把研究问题分解为 N 个子方向 → 多个 worker（DeepSeek/Qwen）
    并行起草各子方向的搜索线索 → 主管合并为统一 JSON 计划（schema 与 _generate_research_plan 一致）。

    返回与 _generate_research_plan 相同的 dict 结构，解析失败返回 None。
    """
    try:
        from ..harness.agents.factory import AgentFactory
        from ..harness.collaboration.supervisor import SupervisorPattern
        from ..harness.events import EventBus
    except Exception:
        logger.warning("Supervisor planning unavailable (harness import failed)", exc_info=True)
        return None

    try:
        factory = AgentFactory(EventBus(), settings)
        supervisor = factory.create_deepseek()
        workers = [factory.create_deepseek(), factory.create_qwen()]

        context_summary = "\n".join(contexts[:3])[:2000]
        input_text = f"研究问题：{question}\n\n已有论文上下文：\n{context_summary}"

        merge_template = (
            "你是研究规划专家。下面是针对同一研究问题的多个子方向线索，"
            "请整合为一份结构化研究计划。严格只输出合法 JSON（不要 markdown 包裹、不要解释），schema：\n"
            '{"understanding":["对该研究需求的理解要点"],"search_plan":'
            '[{"step":1,"action":"具体搜索动作","keywords":"检索关键词"}],'
            '"focus_areas":["重点关注方向"],"estimated_depth":"一般分析/深入综述/对比评估"}\n\n'
            "子方向线索：\n{sub_results}"
        )

        pattern = SupervisorPattern(
            supervisor=supervisor,
            workers=workers,
            event_bus=EventBus(),
            merge_prompt_template=merge_template,
        )
        collab = await pattern.run(input_text)
        if collab.error or not collab.final_output:
            logger.warning("Supervisor planning returned no output: %s", collab.error)
            return None

        raw = str(collab.final_output).strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        import json
        plan = json.loads(raw.strip())
        if "understanding" in plan and "search_plan" in plan:
            return plan
        return None
    except Exception:
        logger.warning("Supervisor research plan generation failed, falling back", exc_info=True)
        return None