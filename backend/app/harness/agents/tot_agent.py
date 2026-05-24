"""ToTAgent — composes DeepSeekAgent + QwenAgent to implement Tree-of-Thoughts."""

from __future__ import annotations

import json
from typing import Any

from .._types import AgentResult, AgentRole, TokenUsage
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent
from .deepseek_agent import DeepSeekAgent
from .qwen_agent import QwenAgent


class ToTAgent(BaseAgent):
    """Tree-of-Thoughts agent that coordinates generation and evaluation.

    Maps the existing ``generate_tot_idea`` three-phase flow onto
    the harness agent lifecycle:
        Phase 1: DeepSeek generates N candidate branches
        Phase 2: Qwen evaluates and scores each branch
        Phase 3: ToT expansion and pruning -> pick winner
    """

    def __init__(
        self,
        generator: DeepSeekAgent,
        evaluator: QwenAgent,
        event_bus: EventBus,
        settings: HarnessSettings,
    ) -> None:
        super().__init__(
            name="ToTAgent",
            role=AgentRole.CRITIC,
            event_bus=event_bus,
            settings=settings,
        )
        self.generator = generator
        self.evaluator = evaluator

    def _helpers(self):
        """Lazy import to avoid pulling heavy pipeline deps at module level."""
        from ...pipeline.tot_generator import (
            extract_first_json_object,
            generate_rule_based_innovation_ideas,
            normalize_tot_candidate,
            to_float,
        )
        from ...pipeline.common_utils import log_token_usage
        return extract_first_json_object, generate_rule_based_innovation_ideas, normalize_tot_candidate, to_float, log_token_usage

    async def _do_run(self, prompt: str, **kwargs: object) -> AgentResult:
        _, generate_rule_based, _, _, _ = self._helpers()
        title = str(kwargs.get("title", ""))
        tags = list(kwargs.get("tags", []))  # type: ignore[arg-type]
        evidence = list(kwargs.get("evidence", []))  # type: ignore[arg-type]

        if not self.settings.enable_tot:
            fallback = generate_rule_based(tags)
            return AgentResult(content=fallback, error="ToT disabled, using rule-based")

        if not self.settings.deepseek_api_key.strip():
            return AgentResult(error="DEEPSEEK_API_KEY not configured")

        candidates, generation_errors = await self._generate_branches(title, tags, evidence)
        if not candidates:
            fallback = generate_rule_based(tags)
            reason = "; ".join(generation_errors[:2]) if generation_errors else "generation failed"
            return AgentResult(content=fallback, error=f"ToT generation failed: {reason}")

        reviewer_scores, overall_comment = await self._evaluate_branches(candidates)
        winner = self._expand_and_prune(candidates, reviewer_scores, overall_comment)

        return AgentResult(
            content=[winner],
            metadata={"collaboration_mode": "ToT"},
        )

    async def _generate_branches(
        self, title: str, tags: list[str], evidence: list[str],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        extract_json, _, normalize, to_f, _ = self._helpers()
        trials = max(1, min(self.settings.tot_generation_trials, 5))
        candidates: list[dict[str, Any]] = []
        errors: list[str] = []

        for idx in range(trials):
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
            result = await self.generator.execute(
                generation_prompt,
                system_prompt="你是顶会级后门攻击学习方法设计专家。",
                temperature=self.settings.tot_generation_temperature,
                max_tokens=900,
            )
            if result.error or not result.content:
                errors.append(f"分支 {idx + 1}: {result.error or 'empty output'}")
                continue

            self._track_usage(result.token_usage)

            payload = extract_json(str(result.content))
            if not payload:
                errors.append(f"分支 {idx + 1}: output not JSON")
                continue

            candidate = normalize(payload, len(candidates))
            candidate["generated_by"] = self.settings.generation_model_name
            candidate["generation_model_id"] = self.settings.deepseek_model
            candidate["generation_step"] = f"[{self.settings.generation_model_name}] Generated branch {idx + 1}"
            candidates.append(candidate)

        return candidates, errors

    async def _evaluate_branches(
        self, candidates: list[dict[str, Any]],
    ) -> tuple[dict[int, dict[str, Any]], str]:
        extract_json, _, _, to_f, _ = self._helpers()
        reviewer_prompt = (
            "你是严谨的顶会评审（Reviewer）。"
            "请对每个候选方案在以下维度打分（1-10）："
            "asr_gain(越高越好)、implementation_cost(越低越好)、stealthiness(越高越好)。"
            "仅输出 JSON。schema:\n"
            '{"scores":[{"index":0,"asr_gain":1,"implementation_cost":1,"stealthiness":1,"comment":"string"}],'
            '"overall_comment":"string"}\n\n'
            f"候选方案: {json.dumps(candidates, ensure_ascii=False)}"
        )
        result = await self.evaluator.execute(
            reviewer_prompt,
            system_prompt="你是客观严谨、以可复现性为核心的审稿人。",
            temperature=self.settings.tot_reviewer_temperature,
            max_tokens=900,
        )

        overall_comment = ""
        reviewer_scores: dict[int, dict[str, Any]] = {}

        if result.error or not result.content:
            return reviewer_scores, f"Reviewer call failed: {result.error or 'empty'}"

        self._track_usage(result.token_usage)

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
        candidates: list[dict[str, Any]],
        reviewer_scores: dict[int, dict[str, Any]],
        overall_comment: str,
    ) -> dict[str, Any]:
        _, _, _, to_f, _ = self._helpers()
        alpha = float(self.settings.tot_score_alpha)
        beta = float(self.settings.tot_score_beta)
        gamma = float(self.settings.tot_score_gamma)

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

        ranked = sorted(candidates, key=lambda c: to_f(c.get("final_score", 0), 0.0), reverse=True)
        keep_count = max(1, min(self.settings.tot_branch_count, len(ranked)))

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

    def _track_usage(self, usage: TokenUsage | None) -> None:
        if usage is None:
            return
        _, _, _, _, log_token_usage = self._helpers()
        log_token_usage(
            project_name=self.settings.app_name,
            model_name=usage.model_name,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )
