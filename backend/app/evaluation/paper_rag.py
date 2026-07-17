"""论文正文 RAG 评估。

评估的是项目里 chat 面板用的那条 RAG 链路。完整复用项目原生组件，确保评的是真实链路：

    PDF → 解析切块 → 入库(向量库+BM25语料) → 混合检索 → DeepSeek 生成答案 → RAGAS 打分

- 解析 PDF：``pipeline.document_parser.extract_text_from_pdf`` + ``chunk_text``
- 入库：``storage.save_chunks``（内部会写 JSON + 调 vector_store.upsert_chunks 进 ChromaDB）
- 检索：``services.chat.retrieve_contexts``（向量 + BM25 混合召回，去重融合）
- 生成：DeepSeek（与 judge 同模型）基于 retrieved contexts 回答
- 评估：RAGAS 的 faithfulness / answer_relevancy / context_precision / context_recall

四项指标含义见本目录 README.md。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..services.chat import retrieve_contexts
from .llm import build_eval_embeddings, build_eval_llm

logger = logging.getLogger("evaluation.paper_rag")


def ingest_paper_for_eval(paper_id: str, pdf_path: str, storage, settings: Settings | None = None) -> list[str]:
    """解析 PDF → 切块 → 入库（文件 + 向量库），返回 chunks。

    这一步是为了让评估有"可被检索的语料"。用项目自己的解析+切块+入库逻辑，
    跟生产跑论文时走的是同一条路径。
    """
    s = settings or get_settings()
    from ..pipeline.document_parser import chunk_text, extract_text_from_pdf

    # 1. 提取纯文本（pypdf 逐页抽取，失败会回退为提示文本）
    text = extract_text_from_pdf(Path(pdf_path))
    if not text.strip():
        raise RuntimeError(f"PDF 解析为空文本：{pdf_path}")
    # 2. 按字符切块（参数来自 settings：max_chunk_chars / chunk_overlap）
    chunks = chunk_text(text, s.max_chunk_chars, s.chunk_overlap)
    chunks = [c for c in chunks if c and c.strip()]
    if not chunks:
        raise RuntimeError("切块结果为空。")
    # 3. 入库：save_chunks 会写 chunks.json + 把向量塞进 ChromaDB
    storage.save_chunks(paper_id, chunks)
    # ⚠️ save_chunks 内部的向量入库被 try/except 静默吞错（生产里是不让向量库故障阻塞主流程），
    #    但评估时如果向量库没真正就绪，检索会全空、指标全 0 却不报错。所以这里显式校验一次。
    vs = getattr(storage, "vector_store", None)
    if vs is not None and not vs.available:
        raise RuntimeError(f"向量库不可用：{getattr(vs, 'unavailable_reason', '未知原因')}")
    logger.info("论文已入库：%s，chunks=%d", paper_id, len(chunks))
    return chunks


def _generate_answer(question: str, contexts: list[str], settings: Settings | None = None) -> str:
    """用 DeepSeek 基于 retrieved contexts 生成答案（与 judge 同模型）。

    这里没有复用项目复杂的流式 chat 链路（带工具调用、HITL 等），而是直接拿检索到的
    contexts 喂给 DeepSeek 做一次问答。一是简单可控，二是评估要的是"给定这些上下文，
    模型能答成什么样"，本来就该去掉工具/检索之外的干扰。
    """
    llm = build_eval_llm(settings)
    # 给每条上下文加序号，方便模型引用，也方便人看
    ctx_block = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts)) if contexts else "(无可用上下文)"
    messages = [
        {
            "role": "system",
            # 关键约束：只许基于给定上下文回答，不许编造——这直接影响 faithfulness 分数
            "content": (
                "你是论文问答助手。严格依据下面给定的论文上下文回答问题；"
                "若上下文不足以回答，就回答「根据提供的上下文无法回答」。"
                "回答简洁、不要编造。"
            ),
        },
        {
            "role": "user",
            "content": f"论文上下文：\n{ctx_block}\n\n问题：{question}",
        },
    ]
    resp = llm.invoke(messages)
    return (getattr(resp, "content", None) or str(resp)).strip()


def run_paper_rag_eval(
    *,
    paper_id: str,
    testset: list[dict[str, Any]],
    storage,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """对论文正文 RAG 跑 RAGAS 四项指标，返回 {metrics, samples, n}。

    流程：对测试集里每个问题，跑 检索→生成，组装成 RAGAS 的 SingleTurnSample，
    最后一次性交给 RAGAS evaluate 算四个指标。

    SingleTurnSample 各字段：
      - user_input        问题
      - response          模型生成的答案（被 faithfulness / answer_relevancy 用）
      - retrieved_contexts 检索到的上下文（被 context_precision 用）
      - reference         金标准答案（被 context_recall 用）
      - reference_contexts 金标准上下文
    """
    s = settings or get_settings()
    samples_ragas: list[Any] = []   # 给 RAGAS 的样本
    per_sample: list[dict[str, Any]] = []  # 给报告的明细（人看的）

    # 第一阶段：对每个问题跑"检索 + 生成"，收集样本
    for item in testset:
        question = item["user_input"]
        # 检索：向量+BM25 混合召回，top_k 来自 settings.eval_paper_top_k
        contexts = retrieve_contexts(question, paper_id, s.eval_paper_top_k, storage)
        # 生成：基于检索到的 contexts 让 DeepSeek 回答
        answer = _generate_answer(question, contexts, s)

        from ragas.dataset_schema import SingleTurnSample

        samples_ragas.append(SingleTurnSample(
            user_input=question,
            response=answer,
            retrieved_contexts=contexts,
            reference=item.get("reference", ""),
            reference_contexts=item.get("reference_contexts", []),
        ))
        per_sample.append({
            "question": question,
            "retrieved_contexts": contexts,
            "answer": answer,
            "reference": item.get("reference", ""),
        })

    if not samples_ragas:
        return {"metrics": {}, "samples": [], "n": 0, "error": "测试集为空"}

    # 第二阶段：RAGAS 评估。注意必须把 list 包成 EvaluationDataset，裸 list 会报错。
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        AnswerRelevancy,     # 答案对问题的相关性
        ContextPrecision,    # 检索结果里相关 context 的精度
        Faithfulness,        # 答案是否忠实于检索到的 context（有没有编造）
        LLMContextRecall,     # 金标准 context 被检索命中的覆盖率（指标名 context_recall）
    )

    eval_llm = build_eval_llm(s)
    embeddings = build_eval_embeddings(s)
    result = evaluate(
        dataset=EvaluationDataset(samples_ragas),   # 包成 EvaluationDataset
        metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), LLMContextRecall()],
        llm=LangchainLLMWrapper(eval_llm),          # judge 用 DeepSeek
        embeddings=LangchainEmbeddingsWrapper(embeddings),
    )

    # evaluate 返回 EvaluationResult，各指标的"平均分"存在私有属性 _repr_dict 里
    # （{metric_name: mean_value}），公开 API 没有直接拿它的方法，所以这里读 _repr_dict。
    repr_dict = getattr(result, "_repr_dict", None) or {}
    metrics: dict[str, float] = {}
    for k, v in repr_dict.items():
        try:
            metrics[str(k).lower()] = float(v)
        except (TypeError, ValueError):
            continue
    logger.info("论文 RAG 指标：%s", metrics)
    return {"metrics": metrics, "samples": per_sample, "n": len(samples_ragas)}
