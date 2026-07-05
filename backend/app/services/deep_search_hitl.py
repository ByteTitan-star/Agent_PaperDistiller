"""Deep search orchestration — HITL checkpoints + native AgentLoop execution."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .deep_search_logger import log_hitl_checkpoint, log_research_plan, log_separator
from .deep_search_planning import (
    build_fallback_answer,
    generate_research_plan,
    generate_research_plan_via_supervisor,
    try_clarify,
)
from .hitl_coordinator import HitlCoordinator
from .hitl_sse import hitl_preview_to_sse

logger = logging.getLogger(__name__)

PRE_REPORT_MIN_SOURCES = 1


def _sse_event(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_sse_payload(event: str) -> dict[str, Any] | None:
    if not event.startswith("data: "):
        return None
    try:
        return json.loads(event[6:].strip())
    except json.JSONDecodeError:
        return None


def _build_agent_contexts(contexts: list[str], history: list[dict[str, str]] | None) -> list[str]:
    agent_contexts = list(contexts)
    if history:
        hist_lines = ["【之前的对话历史】"]
        for msg in history[-10:]:
            role = "用户" if msg["role"] == "user" else "助手"
            hist_lines.append(f"{role}：{msg['content'][:200]}")
        agent_contexts.insert(0, "\n".join(hist_lines))
    return agent_contexts


async def _wait_hitl_with_keepalive(coordinator: HitlCoordinator, hil_id: str) -> AsyncIterator[Any]:
    decision_task = asyncio.create_task(coordinator.wait_for_decision(hil_id, timeout=3600.0))
    while not decision_task.done():
        yield ": hitl_keepalive\n\n"
        await asyncio.sleep(10)
    yield decision_task.result()


async def _await_hitl_decision(
    coordinator: HitlCoordinator,
    hil_id: str,
) -> AsyncIterator[str | Any]:
    decision = None
    try:
        async for item in _wait_hitl_with_keepalive(coordinator, hil_id):
            if isinstance(item, str):
                yield item
            else:
                decision = item
    except TimeoutError:
        yield _sse_event({"type": "error", "text": "等待用户确认超时（1小时），深度搜索已取消。"})
        return
    except Exception as exc:
        yield _sse_event({"type": "error", "text": f"HITL 等待异常：{exc}"})
        return
    if decision is not None:
        yield decision


def _normalize_sources(raw_sources: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_sources:
        if isinstance(item, str):
            url = item.strip()
            title = url
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("title") or "").strip()
            title = str(item.get("title") or url or "搜索结果")
        else:
            continue
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append({"title": title, "url": url})
    return normalized


def _source_from_payload(payload: dict[str, Any]) -> dict[str, str] | None:
    url = str(payload.get("url") or payload.get("title") or "").strip()
    if not url:
        return None
    return {"title": str(payload.get("title") or url), "url": url}


async def _stream_agent_live(
    *,
    run_session_id: str,
    run_question: str,
    agent_contexts: list[str],
    settings: Any,
    user_id: int | None,
    paper_id: str | None,
    history: list[dict[str, str]] | None,
    clarify_hint: str | None,
    defer_done: bool,
) -> AsyncIterator[str]:
    from .agent_chat import agent_deep_search_stream

    done_holder: dict[str, Any] = {}
    async for event in agent_deep_search_stream(
        session_id=run_session_id,
        question=run_question,
        contexts=agent_contexts,
        settings=settings,
        user_id=user_id,
        paper_id=paper_id,
        history=history,
        clarify_hint=clarify_hint,
        defer_done=defer_done,
        done_holder=done_holder if defer_done else None,
    ):
        yield event
    if defer_done and done_holder.get("payload"):
        yield _sse_event(done_holder["payload"])


async def _run_search_with_live_pre_report(
    *,
    coordinator: HitlCoordinator,
    chat_session_id: str,
    user_id: int | None,
    paper_id: str | None,
    run_session_id: str,
    question: str,
    agent_contexts: list[str],
    settings: Any,
    history: list[dict[str, str]] | None,
    clarify_hint: str | None,
) -> AsyncIterator[str]:
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    answer_parts: list[str] = []
    thinking_chain: list[Any] = []
    pre_report_sent = False
    pre_report_hil_id: str | None = None
    decision_task: asyncio.Task[Any] | None = None
    extra_keywords: str | None = None
    generating_phase_sent = False
    last_preview_len = 0

    async for event in _stream_agent_live(
        run_session_id=run_session_id,
        run_question=question,
        agent_contexts=agent_contexts,
        settings=settings,
        user_id=user_id,
        paper_id=paper_id,
        history=history,
        clarify_hint=clarify_hint,
        defer_done=True,
    ):
        payload = _parse_sse_payload(event)

        if payload and payload.get("type") == "source":
            src = _source_from_payload(payload)
            if src and src["url"] not in seen_urls:
                seen_urls.add(src["url"])
                sources.append(src)
                if not pre_report_sent and len(sources) >= PRE_REPORT_MIN_SOURCES:
                    pre_report_sent = True
                    yield _sse_event({"type": "phase", "phase": "generating", "label": "正在生成研究报告..."})
                    generating_phase_sent = True
                    pre_report_hil_id, sse = await coordinator.request(
                        session_id=chat_session_id,
                        user_id=user_id,
                        paper_id=paper_id,
                        hil_event_type="deep_search_pre_report",
                        payload={
                            "question": question,
                            "sources": sources,
                            "answer_preview": "".join(answer_parts)[:500],
                            "phase": "pre_report",
                        },
                        streaming=True,
                        title="边生成边审 — 搜索结果",
                        message="报告正在生成，你可以边看边审。确认来源无误后批准；如需补充检索可填写关键词。",
                    )
                    yield sse
                    decision_task = asyncio.create_task(
                        coordinator.wait_for_decision(pre_report_hil_id, timeout=3600.0)
                    )

        if payload and payload.get("type") == "token":
            if not generating_phase_sent:
                yield _sse_event({"type": "phase", "phase": "generating", "label": "正在生成研究报告..."})
                generating_phase_sent = True
            answer_parts.append(payload.get("text") or "")
            if pre_report_sent and decision_task is not None and not decision_task.done():
                preview = "".join(answer_parts)
                if len(preview) - last_preview_len >= 80:
                    last_preview_len = len(preview)
                    yield hitl_preview_to_sse(
                        checkpoint="pre_report",
                        answer_preview=preview[:500],
                        source_count=len(sources),
                    )

        if payload and payload.get("type") == "done":
            thinking_chain = payload.get("thinking_chain") or thinking_chain
            final_sources = _normalize_sources(payload.get("sources") or sources)
            sources = final_sources or sources
            answer = payload.get("answer") or "".join(answer_parts)

            if decision_task is not None:
                while not decision_task.done():
                    yield ": hitl_keepalive\n\n"
                    await asyncio.sleep(0.5)
                try:
                    decision = decision_task.result()
                except TimeoutError:
                    yield _sse_event({"type": "error", "text": "等待用户确认超时（1小时），深度搜索已取消。"})
                    return
                except Exception as exc:
                    yield _sse_event({"type": "error", "text": f"HITL 等待异常：{exc}"})
                    return

                if decision.action == "rejected":
                    log_hitl_checkpoint("pre_report", "rejected")
                    yield _sse_event({"type": "done", "answer": "用户取消了报告生成。"})
                    return

                if decision.action == "edited" and decision.edited_state:
                    kw = (decision.edited_state.get("extra_keywords") or "").strip()
                    log_hitl_checkpoint("pre_report", "edited", {"extra_keywords": kw})
                    if kw:
                        extra_keywords = kw
                else:
                    log_hitl_checkpoint("pre_report", "approved")

            if extra_keywords:
                yield _sse_event(
                    {
                        "type": "phase",
                        "phase": "searching",
                        "label": f"追加搜索：{extra_keywords}...",
                    }
                )
                extra_sid = f"deep-{uuid.uuid4().hex[:12]}"
                extra_answer_parts: list[str] = []
                async for extra_event in _stream_agent_live(
                    run_session_id=extra_sid,
                    run_question=f"{question} {extra_keywords}",
                    agent_contexts=agent_contexts,
                    settings=settings,
                    user_id=user_id,
                    paper_id=paper_id,
                    history=history,
                    clarify_hint=clarify_hint,
                    defer_done=False,
                ):
                    extra_payload = _parse_sse_payload(extra_event)
                    if extra_payload and extra_payload.get("type") == "token":
                        extra_answer_parts.append(extra_payload.get("text") or "")
                    elif extra_payload and extra_payload.get("type") == "done":
                        extra_answer = extra_payload.get("answer") or "".join(extra_answer_parts)
                        answer = f"{answer}\n\n---\n### 补充搜索结果（关键词：{extra_keywords}）\n\n{extra_answer}"
                        sources.extend(_normalize_sources(extra_payload.get("sources") or []))
                        thinking_chain = list(thinking_chain) + (extra_payload.get("thinking_chain") or [])
                        yield extra_event
                        return
                    yield extra_event
                return

            yield _sse_event(
                {
                    "type": "done",
                    "answer": answer,
                    "thinking_chain": thinking_chain,
                    "sources": sources,
                }
            )
            return

        if payload and payload.get("type") == "error":
            if decision_task and not decision_task.done():
                decision_task.cancel()
            yield event
            return

        yield event


async def stream_deep_search(
    *,
    question: str,
    contexts: list[str],
    settings: Any,
    user_id: int | None,
    paper_id: str | None,
    history: list[dict[str, str]] | None = None,
    hitl_coordinator: HitlCoordinator | None = None,
    chat_session_id: str | None = None,
    agent_session_id: str | None = None,
) -> AsyncIterator[str]:
    """Native deep search with optional HITL checkpoints."""
    log_separator()
    agent_contexts = _build_agent_contexts(contexts, history)
    clarification: str | None = None
    sid = chat_session_id or agent_session_id or f"deep-{uuid.uuid4().hex[:12]}"
    run_sid = agent_session_id or f"run-{uuid.uuid4().hex[:12]}"

    if hitl_coordinator is not None:
        yield _sse_event({"type": "phase", "phase": "planning", "label": "正在分析问题，制定研究计划..."})

        if getattr(settings, "supervisor_planning_enabled", False):
            research_plan = await generate_research_plan_via_supervisor(question, contexts, settings)
            if research_plan is None:
                research_plan = await generate_research_plan(question, contexts, settings)
        else:
            research_plan = await generate_research_plan(question, contexts, settings)

        log_research_plan(research_plan, question)
        if research_plan:
            yield _sse_event({"type": "phase", "phase": "planning", "label": "研究计划已生成，等待确认..."})

        plan_display = (
            {
                "understanding": research_plan.get("understanding", []),
                "search_plan": research_plan.get("search_plan", []),
                "focus_areas": research_plan.get("focus_areas", []),
                "estimated_depth": research_plan.get("estimated_depth", ""),
            }
            if research_plan
            else {
                "understanding": [f"分析问题：{question}"],
                "search_plan": [{"step": 1, "action": "搜索相关问题", "keywords": question}],
                "focus_areas": [],
                "estimated_depth": "一般分析",
            }
        )

        hil_id, sse = await hitl_coordinator.request(
            session_id=sid,
            user_id=user_id,
            paper_id=paper_id,
            hil_event_type="deep_search_pre_search",
            payload={
                "question": question,
                "research_plan": plan_display,
                "phase": "pre_search",
            },
            streaming=False,
            title="📋 研究计划确认",
            message="我分析了你的问题，以下是我的理解和搜索计划，请确认或调整：",
        )
        yield sse

        decision = None
        async for item in _await_hitl_decision(hitl_coordinator, hil_id):
            if isinstance(item, str):
                yield item
            elif isinstance(item, dict) and item.get("type") == "error":
                return
            else:
                decision = item
        if decision is None:
            return

        if decision.action == "rejected":
            log_hitl_checkpoint("pre_search", "rejected")
            yield _sse_event({"type": "done", "answer": "用户取消了深度搜索。"})
            return
        if decision.action == "edited" and decision.edited_state:
            edited_question = decision.edited_state.get("question", "").strip()
            extra_keywords = decision.edited_state.get("extra_keywords", "").strip()
            if edited_question:
                question = edited_question
            if extra_keywords:
                clarification = f"用户补充要求：{extra_keywords}"
            log_hitl_checkpoint("pre_search", "edited", decision.edited_state)
        else:
            log_hitl_checkpoint("pre_search", "approved")

    elif getattr(settings, "react_enable_clarification", True) and len(question) > 15:
        clarification = await try_clarify(question, contexts, settings)
        if clarification:
            yield _sse_event(
                {
                    "type": "phase",
                    "phase": "clarifying",
                    "label": "需要确认研究方向",
                    "detail": clarification,
                }
            )

    yield _sse_event({"type": "phase", "phase": "searching", "label": "正在检索相关资源..."})

    if hitl_coordinator is not None:
        async for event in _run_search_with_live_pre_report(
            coordinator=hitl_coordinator,
            chat_session_id=sid,
            user_id=user_id,
            paper_id=paper_id,
            run_session_id=run_sid,
            question=question,
            agent_contexts=agent_contexts,
            settings=settings,
            history=history,
            clarify_hint=clarification,
        ):
            yield event
        return

    try:
        async for event in _stream_agent_live(
            run_session_id=run_sid,
            run_question=question,
            agent_contexts=agent_contexts,
            settings=settings,
            user_id=user_id,
            paper_id=paper_id,
            history=history,
            clarify_hint=clarification,
            defer_done=False,
        ):
            yield event
    except Exception as exc:
        logger.error("Deep search failed: %s", exc, exc_info=True)
        fallback = build_fallback_answer(question, contexts)
        yield _sse_event(
            {
                "type": "done",
                "answer": f"深度搜索遇到问题：{exc}\n\n以下是基于论文内容的基础回答：\n\n{fallback}",
                "thinking_chain": [f"深度搜索遇到问题：{exc}"],
                "sources": [],
            }
        )
