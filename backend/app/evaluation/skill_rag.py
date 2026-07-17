"""Agent Skill 检索 RAG 评估。

评估的是 ``SkillRegistry.select_tools``：ReAct agent 拿到一个用户意图后，靠语义检索
选出最相关的技能卡。它和论文 RAG 不同——只检索、不生成答案（选完技能就直接执行了），
所以评估以"检索质量"为主：

- skill_recall@k：期望技能有没有出现在 top-k 召回结果里（命中即 1，否则 0，求平均）
- MRR（Mean Reciprocal Rank）：期望技能在召回里的排名倒数，越靠前分越高
- RAGAS LLMContextRecall：把召回到的技能描述当 contexts、期望技能描述当 reference，
  让 LLM 判断金标准信息有没有被检索覆盖

三个指标里前两个是直接算的（不需要 LLM），第三个走 RAGAS（要 LLM 当裁判）。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Settings, get_settings
from .llm import build_eval_embeddings, build_eval_llm

logger = logging.getLogger("evaluation.skill_rag")


def _list_skills(skill_registry) -> list[dict[str, str]]:
    """提取 SkillRegistry 中所有技能的 {tool_name, description}，供合成测试集用。"""
    skills = skill_registry.all_tools()  # list[LoadedSkill]
    return [{"tool_name": s.tool_name, "description": s.description} for s in skills if s.description]


def run_skill_rag_eval(
    *,
    skill_registry,
    testset: list[dict[str, Any]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """对技能检索跑评估，返回 {metrics, samples, n}。

    testset 由 datasets.synthesize_skill_dataset 合成，每条形如
    {query, expected_skill, reference}。
    """
    s = settings or get_settings()
    top_k = s.eval_skill_top_k                  # 召回几个
    min_sim = s.eval_skill_min_similarity       # 相似度低于这个阈值的技能会被丢弃

    hits = 0          # 期望技能命中 top-k 的次数
    mrr_sum = 0.0     # MRR 累加
    per_sample: list[dict[str, Any]] = []  # 给报告的明细
    ragas_samples = []  # 给 RAGAS 的样本

    from ragas.dataset_schema import SingleTurnSample

    # 第一阶段：对每条 query 跑技能检索，算 recall@k 和 MRR
    for item in testset:
        query = item["query"]
        expected = item["expected_skill"]      # 应该被召回的那个技能
        reference = item["reference"]          # 期望技能的描述（作为 ground-truth context）
        # select_tools：先关键词快速匹配，命不中再走 ChromaDB 语义检索
        selected = skill_registry.select_tools(query, top_k, min_sim)
        retrieved_names = [sk.tool_name for sk in selected]
        retrieved_descs = [sk.description for sk in selected]

        # 算 recall@k 和 MRR
        if expected in retrieved_names:
            hits += 1
            rank = retrieved_names.index(expected) + 1  # 1-indexed
            mrr_sum += 1.0 / rank                       # 第 1 名得 1.0，第 2 名 0.5，依此类推
        else:
            rank = 0

        per_sample.append({
            "query": query,
            "expected_skill": expected,
            "retrieved_skills": retrieved_names,
            "hit": expected in retrieved_names,
            "rank": rank,
        })
        # 给 RAGAS 的样本：把召回到的"技能描述"当成检索到的 contexts，
        # 把"期望技能描述"当成 reference（金标准），让 LLM 判断覆盖度。
        ragas_samples.append(SingleTurnSample(
            user_input=query,
            response="",                       # 检索-only 没有答案，留空
            retrieved_contexts=retrieved_descs,
            reference=reference,
        ))

    n = len(testset)
    # 自定义指标（直接算，不调 LLM）
    custom = {
        "skill_recall_at_k": hits / n if n else 0.0,
        "mrr": mrr_sum / n if n else 0.0,
    }

    metrics: dict[str, float] = dict(custom)
    # 第二阶段：RAGAS LLMContextRecall（要调 DeepSeek 当裁判，包在 try 里防止单点失败拖垮整个评估）
    if ragas_samples:
        try:
            from ragas import evaluate
            from ragas.dataset_schema import EvaluationDataset
            from ragas.embeddings import LangchainEmbeddingsWrapper
            from ragas.llms import LangchainLLMWrapper
            from ragas.metrics import LLMContextRecall

            result = evaluate(
                dataset=EvaluationDataset(ragas_samples),  # 同样要包成 EvaluationDataset
                metrics=[LLMContextRecall()],
                llm=LangchainLLMWrapper(build_eval_llm(s)),
                embeddings=LangchainEmbeddingsWrapper(build_eval_embeddings(s)),
            )
            # 取聚合均值（指标名 context_recall），写法同 paper_rag
            repr_dict = getattr(result, "_repr_dict", None) or {}
            for k, v in repr_dict.items():
                try:
                    metrics[str(k).lower()] = float(v)
                except (TypeError, ValueError):
                    continue
        except Exception as exc:
            # RAGAS 这一步失败不影响已经算好的 recall@k / MRR，记一下、把 context_recall 置空
            logger.warning("RAGAS LLMContextRecall 跳过：%s", exc)
            metrics["context_recall"] = None

    logger.info("Skill 检索指标：%s", metrics)
    return {"metrics": metrics, "samples": per_sample, "n": n}
