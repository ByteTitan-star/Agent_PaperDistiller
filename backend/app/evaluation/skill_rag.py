"""Agent Skill 检索 RAG 评估。

SkillRegistry.select_tools 是"检索即用"的语义召回（不生成答案），因此评估以检索质量为主：
- 自定义指标：skill_recall@k（期望技能是否出现在 top-k 召回中）、MRR
- RAGAS LLMContextRecall：把召回的技能描述当作 contexts、期望技能描述当作 reference，LLM 判定覆盖率
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, get_settings
from .llm import build_eval_embeddings, build_eval_llm

logger = logging.getLogger("evaluation.skill_rag")


def _list_skills(skill_registry) -> list[dict[str, str]]:
    """提取 SkillRegistry 中所有技能的 {tool_name, description}。"""
    skills = skill_registry.all_tools()  # list[LoadedSkill]
    return [{"tool_name": s.tool_name, "description": s.description} for s in skills if s.description]


def run_skill_rag_eval(
    *,
    skill_registry,
    testset: list[dict[str, Any]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    s = settings or get_settings()
    top_k = s.eval_skill_top_k
    min_sim = s.eval_skill_min_similarity

    hits = 0
    mrr_sum = 0.0
    per_sample: list[dict[str, Any]] = []
    ragas_samples = []

    from ragas.dataset_schema import SingleTurnSample

    for item in testset:
        query = item["query"]
        expected = item["expected_skill"]
        reference = item["reference"]
        selected = skill_registry.select_tools(query, top_k, min_sim)
        retrieved_names = [sk.tool_name for sk in selected]
        retrieved_descs = [sk.description for sk in selected]

        if expected in retrieved_names:
            hits += 1
            rank = retrieved_names.index(expected) + 1
            mrr_sum += 1.0 / rank
        else:
            rank = 0

        per_sample.append({
            "query": query,
            "expected_skill": expected,
            "retrieved_skills": retrieved_names,
            "hit": expected in retrieved_names,
            "rank": rank,
        })
        ragas_samples.append(SingleTurnSample(
            user_input=query,
            response="",
            retrieved_contexts=retrieved_descs,
            reference=reference,
        ))

    n = len(testset)
    custom = {
        "skill_recall_at_k": hits / n if n else 0.0,
        "mrr": mrr_sum / n if n else 0.0,
    }

    metrics: dict[str, float] = dict(custom)
    if ragas_samples:
        try:
            from ragas import evaluate
            from ragas.dataset_schema import EvaluationDataset
            from ragas.embeddings import LangchainEmbeddingsWrapper
            from ragas.llms import LangchainLLMWrapper
            from ragas.metrics import LLMContextRecall

            result = evaluate(
                dataset=EvaluationDataset(ragas_samples),
                metrics=[LLMContextRecall()],
                llm=LangchainLLMWrapper(build_eval_llm(s)),
                embeddings=LangchainEmbeddingsWrapper(build_eval_embeddings(s)),
            )
            repr_dict = getattr(result, "_repr_dict", None) or {}
            for k, v in repr_dict.items():
                try:
                    metrics[str(k).lower()] = float(v)
                except (TypeError, ValueError):
                    continue
        except Exception as exc:
            logger.warning("RAGAS LLMContextRecall 跳过：%s", exc)
            metrics["context_recall"] = None

    logger.info("Skill 检索指标：%s", metrics)
    return {"metrics": metrics, "samples": per_sample, "n": n}
