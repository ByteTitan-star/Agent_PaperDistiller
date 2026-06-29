"""ToTAgent — Tree-of-Thought（思维树）Agent。

组合 DeepSeekAgent（生成分支候选）+ QwenAgent（评估和打分），
实现 Tree-of-Thoughts 推理策略，三阶段流程：
    阶段 1：DeepSeek 生成 N 个候选分支方案
    阶段 2：Qwen 对每个分支进行评估打分
    阶段 3：综合评分 → 扩展与剪枝 → 选出最优方案

当 ToT 功能未启用或 API 密钥缺失时，自动降级为基于规则的方法。
"""

from __future__ import annotations

import json
from typing import Any

from .._types import AgentResult, AgentRole
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent
from .deepseek_agent import DeepSeekAgent
from .qwen_agent import QwenAgent


class ToTAgent(BaseAgent):
    """Tree-of-Thoughts Agent，协调生成和评估两个子 Agent。

    将现有的 generate_tot_idea 三阶段流程映射到 harness Agent 生命周期：
        阶段 1（_generate_branches）：DeepSeek 生成 N 个候选分支
        阶段 2（_evaluate_branches）：Qwen 评估并对每个分支打分
        阶段 3（_expand_and_prune）：计算综合评分、排序、选出最优分支

    评分公式：Score = α * ASR_Gain - β * Implementation_Cost + γ * Stealthiness
    其中 α、β、γ 由配置中的 tot_score_alpha/beta/gamma 控制。

    Attributes:
        generator: DeepSeek Agent，负责生成候选分支。
        evaluator: Qwen Agent，负责评估和打分。
    """

    def __init__(
        self,
        generator: DeepSeekAgent,     # 生成分支的 Agent（DeepSeek）
        evaluator: QwenAgent,          # 评估分支的 Agent（Qwen）
        event_bus: EventBus,           # 事件总线
        settings: HarnessSettings,     # 框架配置
    ) -> None:
        super().__init__(
            name="ToTAgent",
            role=AgentRole.CRITIC,      # 角色为"评论者"
            event_bus=event_bus,
            settings=settings,
        )
        self.generator = generator
        self.evaluator = evaluator

    def _helpers(self):
        """延迟导入 pipeline 工具函数，避免模块级循环依赖。

        Returns:
            tuple: (extract_first_json_object, generate_rule_based_innovation_ideas,
                    normalize_tot_candidate, to_float)
        """
        from ...pipeline.tot_generator import (
            extract_first_json_object,
            generate_rule_based_innovation_ideas,
            normalize_tot_candidate,
            to_float,
        )
        return extract_first_json_object, generate_rule_based_innovation_ideas, normalize_tot_candidate, to_float

    async def _do_run(self, prompt: str, **kwargs: object) -> AgentResult:
        """执行完整的 ToT 流程：生成 → 评估 → 剪枝。

        Args:
            prompt: 输入提示词（通常包含论文信息）。
            **kwargs: 可选参数：
                - title (str): 论文标题。
                - tags (list[str]): 论文领域标签。
                - evidence (list[str]): 论文证据片段。

        Returns:
            AgentResult: content 为包含最优分支的列表 [winner_dict]，
                metadata 包含 collaboration_mode: "ToT"。
                如果 ToT 未启用或失败，降级返回规则方法的结果。
        """
        _, generate_rule_based, _, _ = self._helpers()
        # 提取论文相关信息
        title = str(kwargs.get("title", ""))
        tags = list(kwargs.get("tags", []))  # type: ignore[arg-type]
        evidence = list(kwargs.get("evidence", []))  # type: ignore[arg-type]
        user_id = kwargs.get("user_id")  # 透传给子 agent，用于 token 记账归属

        # ── 降级检查 1：ToT 功能是否启用 ──
        if not self.settings.enable_tot:
            fallback = generate_rule_based(tags)
            return AgentResult(content=fallback, error="ToT disabled, using rule-based")

        # ── 降级检查 2：DeepSeek API 密钥是否配置 ──
        if not self.settings.deepseek_api_key.strip():
            return AgentResult(error="DEEPSEEK_API_KEY not configured")

        # ── 阶段 1：生成 N 个候选分支 ──
        candidates, generation_errors = await self._generate_branches(title, tags, evidence, user_id)
        if not candidates:
            # 所有分支生成失败，降级为规则方法
            fallback = generate_rule_based(tags)
            reason = "; ".join(generation_errors[:2]) if generation_errors else "generation failed"
            return AgentResult(content=fallback, error=f"ToT generation failed: {reason}")

        # ── 阶段 2：评估并打分 ──
        reviewer_scores, overall_comment = await self._evaluate_branches(candidates, user_id)

        # ── 阶段 3：扩展、剪枝、选出最优 ──
        winner = self._expand_and_prune(candidates, reviewer_scores, overall_comment)

        return AgentResult(
            content=[winner],
            metadata={"collaboration_mode": "ToT"},
        )

    async def _generate_branches(
        self,
        title: str,           # 论文标题
        tags: list[str],       # 领域标签
        evidence: list[str],   # 证据片段
        user_id: int | None = None,  # 用于 token 记账归属
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """阶段 1：使用 DeepSeek 生成多个候选分支方案。

        每次调用 DeepSeek 生成一个独立的创新方案（JSON 格式），
        重试 trials 次以获得多个不同方向的候选。

        Args:
            title: 论文标题。
            tags: 论文领域标签列表。
            evidence: 论文证据片段列表。

        Returns:
            tuple: (candidates, errors)
                - candidates: 成功生成的候选方案列表（已标准化）。
                - errors: 各分支生成失败的错误信息列表。
        """
        extract_json, _, normalize, to_f = self._helpers()
        trials = max(1, min(self.settings.tot_generation_trials, 5))
        candidates: list[dict[str, Any]] = []
        errors: list[str] = []

        for idx in range(trials):
            # 构造生成 prompt，要求输出 JSON 格式的创新方案
            generation_prompt = (
                "你是攻击策略设计者。请输出一个独立的创新方案，且与常见方案显著不同。\n"
                "只输出 JSON，不要解释。JSON schema:\n"
                '{"name":"string","plan":"string","validation":"string","risk":"string",'
                '"asr_gain":1-10,"implementation_cost":1-10,"stealthiness":1-10}\n\n'
                f"分支编号: {idx + 1}\n"
                f"论文标题: {title}\n"
                f"领域标签: {', '.join(tags)}\n"
                f"证据片段: {' | '.join(evidence[:3])}\n"
                "如果是 backdoor 方向，优先考虑 clean-label 投毒、触发器合成、特征碰撞等不同思路。"
            )
            # 调用 DeepSeek 生成分支（token 用量由 generator 的 on_post_run 统一记录）
            result = await self.generator.execute(
                generation_prompt,
                system_prompt="你是顶会级后门攻击学习方法设计专家。",
                temperature=self.settings.tot_generation_temperature,
                max_tokens=900,
                user_id=user_id,
                action_type="tot_generation",
            )
            if result.error or not result.content:
                errors.append(f"分支 {idx + 1}: {result.error or 'empty output'}")
                continue

            # 解析 JSON 输出
            payload = extract_json(str(result.content))
            if not payload:
                errors.append(f"分支 {idx + 1}: output not JSON")
                continue

            # 标准化候选方案并记录来源信息
            candidate = normalize(payload, len(candidates))
            candidate["generated_by"] = self.settings.generation_model_name
            candidate["generation_model_id"] = self.settings.deepseek_model
            candidate["generation_step"] = f"[{self.settings.generation_model_name}] Generated branch {idx + 1}"
            candidates.append(candidate)

        return candidates, errors

    async def _evaluate_branches(
        self,
        candidates: list[dict[str, Any]],  # 待评估的候选分支列表
        user_id: int | None = None,        # 用于 token 记账归属
    ) -> tuple[dict[int, dict[str, Any]], str]:
        """阶段 2：使用 Qwen 对所有候选分支进行评估打分。

        让 Qwen 从 asr_gain（攻击成功率提升）、implementation_cost（实现成本）、
        stealthiness（隐蔽性）三个维度对每个候选打分（1-10 分）。

        Args:
            candidates: 候选分支列表。

        Returns:
            tuple: (reviewer_scores, overall_comment)
                - reviewer_scores: 字典，key 为候选索引，value 为包含各维度分数的字典。
                - overall_comment: 审稿人的总体评价。
        """
        extract_json, _, _, to_f = self._helpers()
        # 构造评估 prompt，要求输出 JSON 格式的评分
        reviewer_prompt = (
            "你是严谨的顶会评审（Reviewer）。"
            "请对每个候选方案在以下维度打分（1-10）："
            "asr_gain(越高越好)、implementation_cost(越低越好)、stealthiness(越高越好)。"
            "仅输出 JSON。schema:\n"
            '{"scores":[{"index":0,"asr_gain":1,"implementation_cost":1,"stealthiness":1,"comment":"string"}],'
            '"overall_comment":"string"}\n\n'
            f"候选方案: {json.dumps(candidates, ensure_ascii=False)}"
        )
        # 调用 Qwen 进行评估（token 用量由 evaluator 的 on_post_run 统一记录）
        result = await self.evaluator.execute(
            reviewer_prompt,
            system_prompt="你是客观严谨、以可复现性为核心的审稿人。",
            temperature=self.settings.tot_reviewer_temperature,
            max_tokens=900,
            user_id=user_id,
            action_type="tot_review",
        )

        overall_comment = ""
        reviewer_scores: dict[int, dict[str, Any]] = {}

        if result.error or not result.content:
            return reviewer_scores, f"Reviewer call failed: {result.error or 'empty'}"

        # 解析评估结果
        review_payload = extract_json(str(result.content)) or {}
        raw_scores = review_payload.get("scores", [])
        if isinstance(raw_scores, list):
            for item in raw_scores:
                if not isinstance(item, dict):
                    continue
                idx = int(to_f(item.get("index", -1), -1))
                if idx < 0 or idx >= len(candidates):
                    continue
                reviewer_scores[idx] = {
                    "asr_gain": to_f(item.get("asr_gain", 5), 5.0),
                    "implementation_cost": to_f(item.get("implementation_cost", 5), 5.0),
                    "stealthiness": to_f(item.get("stealthiness", 5), 5.0),
                    "comment": str(item.get("comment", "")).strip(),
                }
        overall_comment = str(review_payload.get("overall_comment", "")).strip()
        return reviewer_scores, overall_comment

    def _expand_and_prune(
        self,
        candidates: list[dict[str, Any]],      # 所有候选分支
        reviewer_scores: dict[int, dict[str, Any]],  # 审稿评分结果
        overall_comment: str,                    # 审稿总体评价
    ) -> dict[str, Any]:
        """阶段 3：计算综合评分、排序、选出最优分支。

        评分公式：Score = α * ASR_Gain - β * Implementation_Cost + γ * Stealthiness
        系数来自配置 tot_score_alpha / tot_score_beta / tot_score_gamma。

        Args:
            candidates: 候选分支列表。
            reviewer_scores: Qwen 的评分结果，key 为候选索引。
            overall_comment: 审稿人的总体评价文本。

        Returns:
            dict: 最优分支的完整信息（包含评分、来源、选择理由等）。
        """
        _, _, _, to_f = self._helpers()
        # 从配置读取评分系数
        alpha = float(self.settings.tot_score_alpha)
        beta = float(self.settings.tot_score_beta)
        gamma = float(self.settings.tot_score_gamma)

        # 为每个候选计算综合评分
        for idx, candidate in enumerate(candidates):
            review = reviewer_scores.get(idx, {})
            asr_gain = to_f(review.get("asr_gain", candidate.get("asr_gain", 5)), 5.0)
            cost = to_f(review.get("implementation_cost", candidate.get("implementation_cost", 5)), 5.0)
            stealth = to_f(review.get("stealthiness", candidate.get("stealthiness", 5)), 5.0)
            score = alpha * asr_gain - beta * cost + gamma * stealth
            candidate["final_score"] = round(score, 3)
            candidate["asr_gain"] = asr_gain
            candidate["implementation_cost"] = cost
            candidate["stealthiness"] = stealth
            candidate["review_comment"] = str(review.get("comment", "")).strip()
            candidate["evaluated_by"] = self.settings.evaluation_model_name
            candidate["evaluation_model_id"] = self.settings.qwen_model
            candidate["evaluation_step"] = (
                f"[{self.settings.evaluation_model_name}] Evaluated branch {idx + 1}: Score={candidate['final_score']}"
            )

        # 按评分降序排序，选出最优
        ranked = sorted(candidates, key=lambda c: to_f(c.get("final_score", 0), 0.0), reverse=True)
        keep_count = max(1, min(self.settings.tot_branch_count, len(ranked)))

        # 构造最优分支的完整信息
        winner = dict(ranked[0])
        winner["tot_branch_id"] = "B1"
        winner["tot_stage"] = "expanded"
        winner["tot_step"] = f"[ToT] Expanded B1 and kept in top-{keep_count} after pruning."
        winner["source"] = "ToT"
        winner["collaboration_mode"] = (
            f"Multi-Agent Collaboration: "
            f"{self.settings.generation_model_name} (Gen) + {self.settings.evaluation_model_name} (Eval)"
        )
        winner["execution_order"] = (
            f"先生成({self.settings.generation_model_name}) -> "
            f"后评估({self.settings.evaluation_model_name}) -> 再 ToT 分支扩展与剪枝"
        )
        winner["model_steps"] = [
            winner.get("generation_step", ""),
            winner.get("evaluation_step", ""),
            winner.get("tot_step", ""),
        ]
        winner["selection_reason"] = (
            f"Score = {alpha}*ASR_Gain - {beta}*Implementation_Cost + {gamma}*Stealthiness; "
            f"winner score={winner.get('final_score')}; branch=B1. {overall_comment}".strip()
        )
        return winner

    async def aclose(self) -> None:
        """关闭组合的两个子 agent（generator / evaluator）的底层连接。"""
        for sub in (self.generator, self.evaluator):
            try:
                await sub.aclose()
            except Exception:
                pass
