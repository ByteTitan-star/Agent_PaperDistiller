"""测试集合成。

- 论文正文 RAG：用 RAGAS ``TestsetGenerator`` 从 paper chunks 合成 (question, reference, reference_contexts)。
- Skill 检索 RAG：直接用 DeepSeek 为每个技能生成若干"应该召回该技能"的自然语言 query，ground truth = 技能描述。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .llm import build_eval_embeddings, build_eval_llm

logger = logging.getLogger("evaluation.datasets")


def _chunks_to_langchain_docs(chunks: list[str]):
    from langchain_core.documents import Document as LCDocument

    return [LCDocument(page_content=c, metadata={"chunk_index": i}) for i, c in enumerate(chunks) if c.strip()]


def _get_field(sample: Any, key: str, *alts: str) -> Any:
    """从 dict 或对象中按多个候选键名取值。"""
    if isinstance(sample, dict):
        for k in (key, *alts):
            if k in sample and sample[k] is not None:
                return sample[k]
        return None
    for k in (key, *alts):
        v = getattr(sample, k, None)
        if v is not None:
            return v
    return None


def _unwrap(sample: Any) -> Any:
    """Testset 条目可能是 TestsetSample（含 .sample）或 EvaluationDataset 的 SingleTurnSample，统一剥到最内层。"""
    seen: set[int] = set()
    cur = sample
    for _ in range(5):
        inner = getattr(cur, "sample", None)
        if inner is None or id(cur) in seen:
            break
        seen.add(id(cur))
        cur = inner
    return cur


def _extract_sample_fields(sample: Any) -> dict[str, Any]:
    """RAGAS Testset 条目字段在不同版本命名不一，统一抽取为 {user_input, reference, reference_contexts}。"""
    obj = _unwrap(sample)
    user_input = _get_field(obj, "user_input", "query", "question")
    reference = _get_field(obj, "reference_answer", "reference", "ground_truth", "ground_truth_answer")
    ref_ctx = _get_field(obj, "reference_contexts", "ground_truth_contexts")
    if isinstance(ref_ctx, str):
        ref_ctx = [ref_ctx]
    return {
        "user_input": user_input or "",
        "reference": reference or "",
        "reference_contexts": list(ref_ctx or []),
    }


def synthesize_paper_testset(
    chunks: list[str],
    *,
    testset_size: int,
    settings=None,
) -> list[dict[str, Any]]:
    """从论文 chunks 合成测试集，返回 [{user_input, reference, reference_contexts}, ...]。"""
    llm = build_eval_llm(settings)
    embeddings = build_eval_embeddings(settings)

    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.testset import TestsetGenerator

    docs = _chunks_to_langchain_docs(chunks)
    if not docs:
        raise RuntimeError("论文 chunks 为空，无法合成测试集。")

    generator = TestsetGenerator(
        llm=LangchainLLMWrapper(llm),
        embedding_model=LangchainEmbeddingsWrapper(embeddings),
    )

    try:
        testset = generator.generate_with_langchain_docs(  # type: ignore[attr-defined]
            documents=docs,
            testset_size=testset_size,
        )
    except TypeError:
        # 老版本参数名可能是 num_questions
        testset = generator.generate_with_langchain_docs(docs, num_questions=testset_size)  # type: ignore[call-arg]

    samples = []
    # 优先 to_evaluation_dataset()（返回 EvaluationDataset，.samples 为 SingleTurnSample 对象），
    # 其次 to_list()（可能返回 dict），最后退到 .samples 属性或直接迭代。
    iterable = None
    if hasattr(testset, "to_evaluation_dataset"):
        try:
            iterable = testset.to_evaluation_dataset().samples
        except Exception:
            iterable = None
    if iterable is None and hasattr(testset, "to_list"):
        try:
            iterable = testset.to_list()
        except Exception:
            iterable = None
    if iterable is None:
        iterable = getattr(testset, "samples", None) or list(testset)
    for sample in iterable:
        fields = _extract_sample_fields(sample)
        if fields["user_input"]:
            samples.append(fields)
    logger.info("论文测试集合成完成：%d 条", len(samples))
    return samples


def synthesize_skill_dataset(
    skills: list[dict[str, str]],
    *,
    queries_per_skill: int,
    settings=None,
) -> list[dict[str, Any]]:
    """为每个技能合成若干"应召回该技能"的自然语言 query。

    ``skills``: [{tool_name, description}, ...]
    返回 [{query, expected_skill, reference}, ...]，reference=技能描述（作为 ground-truth context）。
    """
    llm = build_eval_llm(settings)
    skill_lines = "\n".join(f"- {s['tool_name']}: {s['description']}" for s in skills)
    prompt = (
        "你是 RAG 评估测试集合成器。下面是一组 agent 技能（tool_name: description）。"
        "请为每个技能生成"
        f"{queries_per_skill}条**应该召回该技能**的自然语言用户 query（描述一个真实场景/意图，"
        "不要直接出现技能 tool_name 本身，要让语义检索去匹配）。"
        "严格只输出 JSON 数组，每个元素形如 "
        '{"query": "...", "expected_skill": "<tool_name>"}。\n\n技能列表：\n'
        f"{skill_lines}"
    )
    resp = llm.invoke(prompt)
    raw = resp.content if hasattr(resp, "content") else str(resp)
    parsed = _parse_json_array(raw)
    # reference = 对应技能的 description
    desc_by_name = {s["tool_name"]: s["description"] for s in skills}
    samples = []
    for item in parsed:
        q = (item.get("query") or "").strip()
        skill_name = (item.get("expected_skill") or "").strip()
        if q and skill_name in desc_by_name:
            samples.append({
                "query": q,
                "expected_skill": skill_name,
                "reference": desc_by_name[skill_name],
            })
    logger.info("Skill 测试集合成完成：%d 条", len(samples))
    return samples


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    """从 LLM 输出中尽量稳健地抽出一个 JSON 数组。"""
    text = raw.strip()
    # 去掉 ```json ... ``` 围栏
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip("`")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except json.JSONDecodeError:
        pass
    # 退一步：抓第一个 [ 到匹配 ]
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, list):
                return [d for d in data if isinstance(d, dict)]
        except json.JSONDecodeError:
            return []
    return []
