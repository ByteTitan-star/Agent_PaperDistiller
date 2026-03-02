import json
import os
import re
from typing import Any

from .common_utils import log_token_usage
from .config import Settings


def generate_rule_based_innovation_ideas(tags: list[str]) -> list[dict[str, str]]:
    """按领域标签生成可执行的创新建议库（回退路径）。"""
    joined_tags = " ".join(tags).lower()
    if "backdoor attack" in joined_tags or "backdoor" in joined_tags:
        return [
            {
                "name": "分布偏移驱动的触发器自适应优化",
                "plan": "将样本分布偏移评分与触发器参数联合优化，按类别动态调整触发器强度和位置。",
                "validation": "在 CIFAR-10/CIFAR-100 上报告 ASR、CA、LPIPS，并与固定触发器对比。",
                "risk": "可能牺牲隐蔽性；通过加入感知约束和人审阈值缓解。",
            },
            {
                "name": "双层优化的防御感知投毒",
                "plan": "外层最大化攻击成功率，内层显式模拟常见检测器（频谱检测/激活聚类）并最小化可检测性。",
                "validation": "在至少 3 种防御下评估攻击前后 ASR 降幅与检测召回率。",
                "risk": "训练开销增大；通过低秩近似与子集采样控制成本。",
            },
            {
                "name": "跨模型迁移后门构造",
                "plan": "使用教师-学生特征一致性损失，提升后门在 ResNet/ViT 间的迁移稳定性。",
                "validation": "报告跨架构迁移矩阵（source→target）的 ASR/CA。",
                "risk": "迁移增强可能降低 clean 精度；引入权重退火平衡。",
            },
            {
                "name": "物理世界鲁棒触发器",
                "plan": "训练加入打印、压缩、模糊、视角变化增强，构建可落地触发器。",
                "validation": "仿真 + 实拍两阶段评估，输出真实场景 ASR。",
                "risk": "实验成本高；先在仿真管线筛选候选触发器。",
            },
            {
                "name": "后门风险评分与预警系统",
                "plan": "构建样本级风险分数（特征残差 + 决策边界不稳定性），用于训练前后门预筛查。",
                "validation": "在含后门与干净数据混合场景下评估 AUC/F1。",
                "risk": "误报率偏高；采用分层阈值和人工复核流程。",
            },
        ]

    return [
        {
            "name": "数据覆盖增强",
            "plan": "按难样本分层采样并补齐长尾分布，减少方法对单一数据分布的过拟合。",
            "validation": "对比扩充前后在 OOD 测试集上的指标变化。",
            "risk": "数据成本上升；优先引入高收益子集。",
        },
        {
            "name": "多目标训练策略",
            "plan": "把性能、鲁棒性、可解释性纳入统一损失函数并进行权重搜索。",
            "validation": "绘制 Pareto 前沿并给出权衡点。",
            "risk": "超参数敏感；采用网格+贝叶斯混合搜索。",
        },
        {
            "name": "复现工程化",
            "plan": "固定随机种子、配置模板化、日志结构化，降低实验不可重复风险。",
            "validation": "跨 3 次重复实验报告方差。",
            "risk": "工程改造周期增加；分阶段推进。",
        },
    ]


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    """从 LLM 文本中抽取第一个 JSON 对象。"""
    if not text.strip():
        return None

    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*```$", "", normalized)

    try:
        payload = json.loads(normalized)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    left = normalized.find("{")
    right = normalized.rfind("}")
    if left < 0 or right < 0 or right <= left:
        return None

    candidate = normalized[left : right + 1]
    try:
        payload = json.loads(candidate)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        return None


def to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_multi_agent_collaboration_label(settings: Settings) -> str:
    return (
        f"Multi-Agent Collaboration: "
        f"{settings.generation_model_name} (Gen) + {settings.evaluation_model_name} (Eval)"
    )


def normalize_tot_candidate(item: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "name": str(item.get("name", f"候选方案 {index + 1}")).strip() or f"候选方案 {index + 1}",
        "plan": str(item.get("plan", "N/A")).strip() or "N/A",
        "validation": str(item.get("validation", "N/A")).strip() or "N/A",
        "risk": str(item.get("risk", "N/A")).strip() or "N/A",
        "asr_gain": to_float(item.get("asr_gain", item.get("ASR_Gain", 5)), 5.0),
        "implementation_cost": to_float(
            item.get("implementation_cost", item.get("Implementation_Cost", 5)),
            5.0,
        ),
        "stealthiness": to_float(item.get("stealthiness", item.get("Stealthiness", 5)), 5.0),
    }


def generate_tot_idea(
    title: str,
    tags: list[str],
    evidence: list[str],
    settings: Settings,
) -> tuple[list[dict[str, Any]], str | None]:
    """严格 ToT：先生成 -> 后评估 -> 再 ToT 扩展与剪枝。"""
    if not settings.enable_tot:
        return [], "ToT 已关闭"
    deepseek_key = settings.deepseek_api_key.strip()
    if not deepseek_key:
        return [], "DEEPSEEK_API_KEY 未配置"
    qwen_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not qwen_key:
        return [], "DASHSCOPE_API_KEY 未配置"

    try:
        from openai import OpenAI
    except Exception as exc:
        return [], f"缺少 openai SDK: {exc}"

    try:
        generation_client = OpenAI(
            api_key=deepseek_key,
            base_url=settings.deepseek_base_url.rstrip("/"),
            timeout=settings.deepseek_timeout_sec,
        )
        evaluation_client = OpenAI(
            api_key=qwen_key,
            base_url=settings.qwen_base_url.rstrip("/"),
            timeout=settings.qwen_timeout_sec,
        )
    except Exception as exc:
        return [], f"ToT 客户端初始化失败: {exc}"

    generation_agent = settings.generation_model_name
    evaluation_agent = settings.evaluation_model_name
    collaboration_mode = build_multi_agent_collaboration_label(settings)

    # 阶段 1：生成分支
    trials = max(1, min(settings.tot_generation_trials, 5))
    candidates: list[dict[str, Any]] = []
    generation_errors: list[str] = []
    for idx in range(trials):
        generation_prompt = (
            "你是攻击策略设计者。请输出一个独立的创新方案，且与常见方案显著不同。\n"
            "只输出 JSON，不要解释。JSON schema:\n"
            "{\n"
            '  "name": "string",\n'
            '  "plan": "string",\n'
            '  "validation": "string",\n'
            '  "risk": "string",\n'
            '  "asr_gain": 1-10,\n'
            '  "implementation_cost": 1-10,\n'
            '  "stealthiness": 1-10\n'
            "}\n\n"
            f"分支编号: {idx + 1}\n"
            f"论文标题: {title}\n"
            f"领域标签: {', '.join(tags)}\n"
            f"证据片段: {' | '.join(evidence[:3])}\n"
            "如果是 backdoor 方向，优先考虑 clean-label 投毒、触发器合成、特征碰撞等不同思路。"
        )
        try:
            response = generation_client.chat.completions.create(
                model=settings.deepseek_model,
                messages=[
                    {"role": "system", "content": "你是顶会级后门攻击学习方法设计专家。"},
                    {"role": "user", "content": generation_prompt},
                ],
                temperature=settings.tot_generation_temperature,
                max_tokens=900,
            )

            if response.usage:
                log_token_usage(
                    project_name=settings.app_name,
                    model_name=settings.deepseek_model,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                )

            content = (response.choices[0].message.content or "").strip()
            payload = extract_first_json_object(content)
            if not payload:
                generation_errors.append(f"分支 {idx + 1}: 输出非 JSON")
                continue
            candidate = normalize_tot_candidate(payload, len(candidates))
            candidate["generated_by"] = generation_agent
            candidate["generation_model_id"] = settings.deepseek_model
            candidate["generation_step"] = f"[{generation_agent}] Generated branch {idx + 1}"
            candidates.append(candidate)
        except Exception as exc:  # pragma: no cover - runtime/network dependent
            generation_errors.append(f"分支 {idx + 1}: {exc}")

    if len(candidates) < 1:
        reason = "；".join(generation_errors[:2]) if generation_errors else "生成阶段失败"
        return [], f"ToT 生成阶段失败: {reason}"

    # 阶段 2：评审打分
    reviewer_prompt = (
        "你是严谨的顶会评审（Reviewer）。"
        "请对每个候选方案在以下维度打分（1-10）："
        "asr_gain(越高越好)、implementation_cost(越低越好)、stealthiness(越高越好)。"
        "仅输出 JSON。schema:\n"
        "{\n"
        '  "scores": [\n'
        "    {\n"
        '      "index": 0,\n'
        '      "asr_gain": 1,\n'
        '      "implementation_cost": 1,\n'
        '      "stealthiness": 1,\n'
        '      "comment": "string"\n'
        "    }\n"
        "  ],\n"
        '  "overall_comment": "string"\n'
        "}\n\n"
        f"候选方案: {json.dumps(candidates, ensure_ascii=False)}"
    )

    reviewer_scores: dict[int, dict[str, Any]] = {}
    overall_comment = ""
    try:
        review_response = evaluation_client.chat.completions.create(
            model=settings.qwen_model,
            messages=[
                {"role": "system", "content": "你是客观严谨、以可复现性为核心的审稿人。"},
                {"role": "user", "content": reviewer_prompt},
            ],
            temperature=settings.tot_reviewer_temperature,
            max_tokens=900,
        )

        if review_response.usage:
            log_token_usage(
                project_name=settings.app_name,
                model_name=settings.qwen_model,
                prompt_tokens=review_response.usage.prompt_tokens,
                completion_tokens=review_response.usage.completion_tokens,
            )

        review_content = (review_response.choices[0].message.content or "").strip()
        review_payload = extract_first_json_object(review_content) or {}
        raw_scores = review_payload.get("scores", [])
        if isinstance(raw_scores, list):
            for item in raw_scores:
                if not isinstance(item, dict):
                    continue
                idx = int(to_float(item.get("index", -1), -1))
                if idx < 0 or idx >= len(candidates):
                    continue
                reviewer_scores[idx] = {
                    "asr_gain": to_float(item.get("asr_gain", 5), 5.0),
                    "implementation_cost": to_float(item.get("implementation_cost", 5), 5.0),
                    "stealthiness": to_float(item.get("stealthiness", 5), 5.0),
                    "comment": str(item.get("comment", "")).strip(),
                }
        overall_comment = str(review_payload.get("overall_comment", "")).strip()
    except Exception as exc:  # pragma: no cover - runtime/network dependent
        overall_comment = f"Reviewer 调用失败，已使用候选自评: {exc}"

    # 阶段 3：ToT 扩展与剪枝
    alpha = float(settings.tot_score_alpha)
    beta = float(settings.tot_score_beta)
    gamma = float(settings.tot_score_gamma)

    for idx, candidate in enumerate(candidates):
        review = reviewer_scores.get(idx, {})
        asr_gain = to_float(review.get("asr_gain", candidate.get("asr_gain", 5)), 5.0)
        implementation_cost = to_float(
            review.get("implementation_cost", candidate.get("implementation_cost", 5)),
            5.0,
        )
        stealthiness = to_float(review.get("stealthiness", candidate.get("stealthiness", 5)), 5.0)
        score = alpha * asr_gain - beta * implementation_cost + gamma * stealthiness
        candidate["final_score"] = round(score, 3)
        candidate["asr_gain"] = asr_gain
        candidate["implementation_cost"] = implementation_cost
        candidate["stealthiness"] = stealthiness
        candidate["review_comment"] = str(review.get("comment", "")).strip()
        candidate["evaluated_by"] = evaluation_agent
        candidate["evaluation_model_id"] = settings.qwen_model
        candidate["evaluation_step"] = (
            f"[{evaluation_agent}] Evaluated branch {idx + 1}: Score={candidate['final_score']}"
        )

    ranked_candidates = sorted(
        candidates,
        key=lambda item: to_float(item.get("final_score", 0), 0.0),
        reverse=True,
    )
    keep_count = max(1, min(settings.tot_branch_count, len(ranked_candidates)))
    expanded_candidates: list[dict[str, Any]] = []
    for rank, candidate in enumerate(ranked_candidates[:keep_count], start=1):
        branch = dict(candidate)
        branch["tot_branch_id"] = f"B{rank}"
        branch["tot_stage"] = "expanded"
        branch["tot_step"] = (
            f"[ToT] Expanded {branch['tot_branch_id']} and kept in top-{keep_count} after pruning."
        )
        expanded_candidates.append(branch)

    if not expanded_candidates:
        return [], "ToT 分支扩展失败"

    winner = expanded_candidates[0]
    winner["source"] = "ToT"
    winner["collaboration_mode"] = collaboration_mode
    winner["execution_order"] = (
        f"先生成({generation_agent}) -> 后评估({evaluation_agent}) -> 再 ToT 分支扩展与剪枝"
    )
    winner["model_steps"] = [
        winner.get("generation_step", ""),
        winner.get("evaluation_step", ""),
        winner.get("tot_step", ""),
    ]
    winner["selection_reason"] = (
        f"Score = {alpha}*ASR_Gain - {beta}*Implementation_Cost + {gamma}*Stealthiness; "
        f"winner score={winner.get('final_score')}; branch={winner.get('tot_branch_id')}. "
        f"{overall_comment}".strip()
    )
    return [winner], None


def generate_innovation_ideas(
    title: str,
    tags: list[str],
    evidence: list[str],
    settings: Settings | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """优先 ToT，失败回退规则库。"""
    if settings:
        ideas, reason = generate_tot_idea(title=title, tags=tags, evidence=evidence, settings=settings)
        if ideas:
            return ideas, None
        fallback = generate_rule_based_innovation_ideas(tags)
        return fallback, reason

    return generate_rule_based_innovation_ideas(tags), "未提供 settings，使用规则库"


__all__ = [
    "generate_rule_based_innovation_ideas",
    "extract_first_json_object",
    "to_float",
    "build_multi_agent_collaboration_label",
    "normalize_tot_candidate",
    "generate_tot_idea",
    "generate_innovation_ideas",
]

