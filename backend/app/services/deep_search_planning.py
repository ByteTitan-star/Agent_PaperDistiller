"""Deep search planning helpers — research plans, clarification, fallbacks."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_fallback_answer(question: str, contexts: list[str], reason: str | None = None) -> str:
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


async def try_clarify(question: str, contexts: list[str], settings: Any) -> str | None:
    """LLM decides whether clarification is needed. Returns hint text or None."""
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
                {
                    "role": "user",
                    "content": CLARIFY_USER.format(
                        context=context_summary,
                        question=question,
                    ),
                },
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


async def generate_research_plan(question: str, contexts: list[str], settings: Any) -> dict | None:
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
                {
                    "role": "user",
                    "content": RESEARCH_PLAN_USER.format(
                        context=context_summary,
                        question=question,
                    ),
                },
            ],
            max_tokens=600,
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        plan = json.loads(raw.strip())
        if "understanding" in plan and "search_plan" in plan:
            return plan
        return None
    except Exception:
        logger.warning("Research plan generation failed, skipping", exc_info=True)
        return None


async def generate_research_plan_via_supervisor(question: str, contexts: list[str], settings: Any) -> dict | None:
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
        plan = json.loads(raw.strip())
        if "understanding" in plan and "search_plan" in plan:
            return plan
        return None
    except Exception:
        logger.warning("Supervisor research plan generation failed, falling back", exc_info=True)
        return None
