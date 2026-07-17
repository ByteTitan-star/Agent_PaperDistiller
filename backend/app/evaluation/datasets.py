"""测试集合成。

RAG 评估需要"金标准"测试集：一组 (问题, 期望答案, 期望上下文)。本模块负责生成它们。

两套 RAG 用不同的合成策略：

- 论文正文 RAG：用 RAGAS 自带的 ``TestsetGenerator``。它把 paper chunks 当文档，
  自动跑 NER/主题抽取 → 嵌入聚类 → 生成 persona → 合成问题与参考答案。
  这是 RAGAS 原生的方式，输出形如 (user_input, reference, reference_contexts)。

- Skill 检索 RAG：RAGAS 生成器不适用（技能是"检索即用"、没有自然语言答案），
  所以这里直接 prompt DeepSeek：给每个技能生成若干"应该召回该技能"的自然语言 query，
  ground truth = 技能描述。这是手写合成，更贴合检索-only 场景。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .llm import build_eval_embeddings, build_eval_llm

logger = logging.getLogger("evaluation.datasets")


# ---------------------------------------------------------------------------
# 论文测试集：RAGAS TestsetGenerator
# ---------------------------------------------------------------------------

def _chunks_to_langchain_docs(chunks: list[str]):
    """把纯文本 chunks 转成 RAGAS/LangChain 认的 Document 对象。

    RAGAS 的 TestsetGenerator 接收的是 LangChain ``Document``（必须用 ``page_content`` 字段），
    所以这里做一层转换。metadata 里记一下 chunk 序号，方便后续追溯。
    """
    from langchain_core.documents import Document as LCDocument

    return [LCDocument(page_content=c, metadata={"chunk_index": i}) for i, c in enumerate(chunks) if c.strip()]


def _get_field(sample: Any, key: str, *alts: str) -> Any:
    """从 dict 或对象中按多个候选键名取值（兼容不同 RAGAS 版本的字段命名）。"""
    # dict 情况：按 key/候选键 查
    if isinstance(sample, dict):
        for k in (key, *alts):
            if k in sample and sample[k] is not None:
                return sample[k]
        return None
    # 对象情况：按属性名取
    for k in (key, *alts):
        v = getattr(sample, k, None)
        if v is not None:
            return v
    return None


def _unwrap(sample: Any) -> Any:
    """把 Testset 条目剥到最内层。

    RAGAS 的 Testset 条目可能是 TestsetSample（外层包装，真正内容在 ``.sample`` 属性里），
    也可能直接是 EvaluationDataset 里的 SingleTurnSample。这里循环往里剥 5 层，
    确保拿到能取 user_input/reference 的那个对象。
    """
    seen: set[int] = set()
    cur = sample
    for _ in range(5):
        inner = getattr(cur, "sample", None)
        if inner is None or id(cur) in seen:  # 没有内层 或 出现环，停止
            break
        seen.add(id(cur))
        cur = inner
    return cur


def _extract_sample_fields(sample: Any) -> dict[str, Any]:
    """把一个 RAGAS Testset 条目统一抽成 {user_input, reference, reference_contexts}。

    不同 RAGAS 版本里这些字段叫法不一致（user_input/query、reference/reference_answer/
    ground_truth 等），这里把候选名都列出来兼容。
    """
    obj = _unwrap(sample)
    user_input = _get_field(obj, "user_input", "query", "question")  # 合成出的问题
    reference = _get_field(obj, "reference_answer", "reference", "ground_truth", "ground_truth_answer")  # 参考答案
    ref_ctx = _get_field(obj, "reference_contexts", "ground_truth_contexts")  # 参考上下文（金标准 chunk）
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
    """从论文 chunks 合成测试集，返回 [{user_input, reference, reference_contexts}, ...]。

    流程：chunks → LangChain Document → RAGAS TestsetGenerator 自动合成 → 抽取字段。
    ``testset_size`` 控制合成多少条问题（每条都会多次调用 DeepSeek，成本随条数增长）。
    """
    # judge 和合成器聚类都复用 llm.py 里构造的 DeepSeek + 本地嵌入
    llm = build_eval_llm(settings)
    embeddings = build_eval_embeddings(settings)

    # RAGAS 要求把 LLM/嵌入用 Wrapper 包一层才认
    from ragas.llms import LangchainLLMWrapper
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.testset import TestsetGenerator

    docs = _chunks_to_langchain_docs(chunks)
    if not docs:
        raise RuntimeError("论文 chunks 为空，无法合成测试集。")

    generator = TestsetGenerator(
        llm=LangchainLLMWrapper(llm),                       # 合成问题/答案用 DeepSeek
        embedding_model=LangchainEmbeddingsWrapper(embeddings),  # 聚类文档用本地嵌入
    )

    # generate_with_langchain_docs：RAGAS 0.2.x 参数叫 num_questions，0.4.x 改成 testset_size
    try:
        testset = generator.generate_with_langchain_docs(  # type: ignore[attr-defined]
            documents=docs,
            testset_size=testset_size,
        )
    except TypeError:
        # 老版本参数名是 num_questions
        testset = generator.generate_with_langchain_docs(docs, num_questions=testset_size)  # type: ignore[call-arg]

    # 把 RAGAS 生成的 Testset 转成我们要的 dict 列表。不同版本暴露条目的方式不同，
    # 按"对象 → dict → 直接迭代"的优先级取一个可迭代对象。
    samples = []
    iterable = None
    # 优先 to_evaluation_dataset()：返回 EvaluationDataset，.samples 是 SingleTurnSample 对象
    if hasattr(testset, "to_evaluation_dataset"):
        try:
            iterable = testset.to_evaluation_dataset().samples
        except Exception:
            iterable = None
    # 其次 to_list()：某些版本返回的是 dict 列表
    if iterable is None and hasattr(testset, "to_list"):
        try:
            iterable = testset.to_list()
        except Exception:
            iterable = None
    # 兜底：直接取 .samples 属性，或把对象本身当可迭代
    if iterable is None:
        iterable = getattr(testset, "samples", None) or list(testset)
    for sample in iterable:
        fields = _extract_sample_fields(sample)
        if fields["user_input"]:  # 过滤掉没合成出问题的空条目
            samples.append(fields)
    logger.info("论文测试集合成完成：%d 条", len(samples))
    return samples


# ---------------------------------------------------------------------------
# 技能测试集：手写 LLM 合成（RAGAS 生成器不适用检索-only 场景）
# ---------------------------------------------------------------------------

def synthesize_skill_dataset(
    skills: list[dict[str, str]],
    *,
    queries_per_skill: int,
    settings=None,
) -> list[dict[str, Any]]:
    """为每个技能合成若干"应召回该技能"的自然语言 query。

    为什么不用 RAGAS 生成器：技能检索是"检索即用"，没有自然语言答案，RAGAS 的
    TestsetGenerator 是围绕"问答对"设计的，套不上。所以这里直接 prompt DeepSeek：
    给它技能列表，让它为每个技能生成若干真实场景的 query（且不让它直接说出技能名，
    强制语义检索去匹配），再用技能描述作为 ground-truth context。

    Args:
        skills: [{tool_name, description}, ...]
        queries_per_skill: 每个技能生成几条 query
    返回 [{query, expected_skill, reference}, ...]，reference=技能描述。
    """
    llm = build_eval_llm(settings)
    # 把技能列表拼成 "- tool_name: description" 的文本喂给 LLM
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
    # 用技能描述作为 reference（ground-truth context），供后续 context_recall 用
    desc_by_name = {s["tool_name"]: s["description"] for s in skills}
    samples = []
    for item in parsed:
        q = (item.get("query") or "").strip()
        skill_name = (item.get("expected_skill") or "").strip()
        # 只保留 LLM 输出的 expected_skill 确实存在于技能表里的条目（防止幻觉）
        if q and skill_name in desc_by_name:
            samples.append({
                "query": q,
                "expected_skill": skill_name,
                "reference": desc_by_name[skill_name],
            })
    logger.info("Skill 测试集合成完成：%d 条", len(samples))
    return samples


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    """从 LLM 输出中尽量稳健地抽出一个 JSON 数组。

    LLM 经常会把 JSON 包在 ```json ... ``` 围栏里，或者前后带废话，所以这里三段式兜底：
    1. 去围栏后整体 json.loads；
    2. 失败就抓第一个 '[' 到最后一个 ']' 之间再试；
    3. 还失败就返回空列表。
    """
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
