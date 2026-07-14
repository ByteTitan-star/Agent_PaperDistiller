"""论文正文 RAG 评估。

链路（忠实复用项目原生组件）：
- 解析 PDF：``pipeline.document_parser.extract_text_from_pdf`` + ``chunk_text``
- 入库：``storage.save_chunks`` + ``storage.upsert_chunks``（ChromaDB + BM25 语料）
- 检索：``services.chat.retrieve_contexts``（向量 + BM25 混合召回）
- 生成：DeepSeek（与 judge 同模型）基于 contexts 回答
- 评估：RAGAS faithfulness / answer_relevancy / context_precision / context_recall
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
    """解析 PDF → 切块 → 入库（文件 + 向量库），返回 chunks。"""
    s = settings or get_settings()
    from ..pipeline.document_parser import chunk_text, extract_text_from_pdf

    text = extract_text_from_pdf(Path(pdf_path))
    if not text.strip():
        raise RuntimeError(f"PDF 解析为空文本：{pdf_path}")
    chunks = chunk_text(text, s.max_chunk_chars, s.chunk_overlap)
    chunks = [c for c in chunks if c and c.strip()]
    if not chunks:
        raise RuntimeError("切块结果为空。")
    storage.save_chunks(paper_id, chunks)
    # save_chunks 内部的向量入库被静默吞错，这里显式校验向量库是否真正就绪
    vs = getattr(storage, "vector_store", None)
    if vs is not None and not vs.available:
        raise RuntimeError(f"向量库不可用：{getattr(vs, 'unavailable_reason', '未知原因')}")
    logger.info("论文已入库：%s，chunks=%d", paper_id, len(chunks))
    return chunks


def _generate_answer(question: str, contexts: list[str], settings: Settings | None = None) -> str:
    """用 DeepSeek 基于 retrieved contexts 生成答案（与 judge 同模型）。"""
    llm = build_eval_llm(settings)
    ctx_block = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts)) if contexts else "(无可用上下文)"
    messages = [
        {
            "role": "system",
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
    """对论文正文 RAG 跑 RAGAS 四项指标，返回 {metrics, samples, n}。"""
    s = settings or get_settings()
    samples_ragas = []
    per_sample: list[dict[str, Any]] = []

    for item in testset:
        question = item["user_input"]
        contexts = retrieve_contexts(question, paper_id, s.eval_paper_top_k, storage)
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

    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import (
        AnswerRelevancy,
        ContextPrecision,
        Faithfulness,
        LLMContextRecall,
    )

    eval_llm = build_eval_llm(s)
    embeddings = build_eval_embeddings(s)
    result = evaluate(
        dataset=EvaluationDataset(samples_ragas),
        metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), LLMContextRecall()],
        llm=LangchainLLMWrapper(eval_llm),
        embeddings=LangchainEmbeddingsWrapper(embeddings),
    )

    # EvaluationResult._repr_dict 是各指标的聚合均值 {metric_name: mean}
    repr_dict = getattr(result, "_repr_dict", None) or {}
    metrics: dict[str, float] = {}
    for k, v in repr_dict.items():
        try:
            metrics[str(k).lower()] = float(v)
        except (TypeError, ValueError):
            continue
    logger.info("论文 RAG 指标：%s", metrics)
    return {"metrics": metrics, "samples": per_sample, "n": len(samples_ragas)}
