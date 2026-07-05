"""Agent-backed chat and deep search — native ReAct runtime."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from ..agent.bootstrap import get_runtime
from ..agent.loop import TurnConfig
from ..agent.schemas import AuthContext, EventType
from ..harness.react.prompts import REACT_SYSTEM_PROMPT
from ..services.user_settings import load_user_settings

logger = logging.getLogger("agent_chat")


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _settings_for_turn(settings: Any, user_id: int | None) -> Any:
    if not user_id:
        return settings
    try:
        return await load_user_settings(user_id, fallback=settings)
    except ValueError:
        return settings


async def agent_deep_search_stream(
    *,
    session_id: str,
    question: str,
    contexts: list[str],
    settings: Any,
    user_id: int | None,
    paper_id: str | None,
    history: list[dict[str, str]] | None = None,
    hitl_manager: Any | None = None,
    clarify_hint: str | None = None,
    defer_done: bool = False,
    done_holder: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Deep search via native AgentLoop + streaming SSE.

    When *defer_done* is True, tokens/tools/sources stream live but the final
    ``done`` (or ``error``) payload is stored in *done_holder* under key
    ``payload`` instead of being yielded.
    """
    runtime = get_runtime()
    user_settings = await _settings_for_turn(settings, user_id)
    auth = AuthContext(user_id=user_id or 0, paper_id=paper_id)

    context_block = "\n\n".join(contexts[:12])
    system = REACT_SYSTEM_PROMPT + f"\n\n## Paper context\n{context_block}"

    user_message = question
    if clarify_hint:
        user_message = f"{question}\n\n[研究方向提示] {clarify_hint}"

    hitl_checkpoints: dict = {}

    tool_mask = {
        "web_search",
        "arxiv_search_tool",
        "run_skill",
        "spawn_sub_agent",
        "wait_sub_agents",
    }
    if runtime.sandbox_manager and runtime.sandbox_manager.settings.is_configured():
        tool_mask.update({"execute_code", "shell_command"})

    turn = TurnConfig(
        system_prompt=system,
        tool_mask=tool_mask,
        max_iterations=getattr(settings, "agent_max_iterations", 12),
        deep_search=True,
        user_settings=user_settings,
        hitl_checkpoints=hitl_checkpoints,
    )

    if hitl_manager is not None:
        yield _sse_event({"type": "stage", "stage": "planning", "message": "制定研究计划…"})

    queue: asyncio.Queue = asyncio.Queue()

    async def pump_events():
        async for event in runtime.stream_bus.subscribe(session_id):
            await queue.put(event)

    pump_task = asyncio.create_task(pump_events())

    run_task = asyncio.create_task(
        runtime.loop.run_streaming(
            session_id,
            user_message,
            auth,
            turn_config=turn,
        )
    )

    answer_parts: list[str] = []
    thinking_chain: list[dict] = []
    sources: list[str] = []
    hold_done = defer_done and done_holder is not None
    if hold_done:
        done_holder.setdefault("payload", None)

    try:
        while not run_task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except TimeoutError:
                continue

            if event.event_type == EventType.TEXT:
                delta = event.data.get("delta", False)
                text = event.data.get("text") or ""
                if delta:
                    answer_parts.append(text)
                    yield _sse_event({"type": "token", "text": text})
                else:
                    answer_parts.append(text)
            elif event.event_type == EventType.STAGE and event.data.get("history_unavailable"):
                yield _sse_event(
                    {
                        "type": "stage",
                        "stage": "context",
                        "history_unavailable": True,
                        "message": event.data.get("message"),
                    }
                )
            elif event.event_type == EventType.THINKING:
                thinking_chain.append({"type": "thinking", "content": event.data.get("text")})
                yield _sse_event({"type": "thinking", "text": event.data.get("text")})
            elif event.event_type == EventType.TOOL_CALL:
                tool_name = event.data.get("name")
                thinking_chain.append(
                    {
                        "type": "tool_call",
                        "name": tool_name,
                        "arguments": event.data.get("arguments"),
                    }
                )
                yield _sse_event({"type": "tool", "name": tool_name, "query": tool_name})
            elif event.event_type == EventType.TOOL_RESULT:
                content = event.data.get("content") or ""
                thinking_chain.append({"type": "tool_result", "content": content[:300]})
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("http"):
                        sources.append(line)
                        yield _sse_event({"type": "source", "title": line, "url": line})
            elif event.event_type == EventType.DONE:
                answer = event.data.get("answer") or "".join(answer_parts)
                done_payload = {
                    "type": "done",
                    "answer": answer,
                    "thinking_chain": event.data.get("thinking_chain") or thinking_chain,
                    "sources": sources[:20],
                }
                if hold_done:
                    done_holder["payload"] = done_payload
                else:
                    yield _sse_event(done_payload)
                break
            elif event.event_type == EventType.ERROR:
                err_payload = {"type": "error", "text": event.data.get("message")}
                if hold_done:
                    done_holder["payload"] = err_payload
                else:
                    yield _sse_event(err_payload)
                break
    finally:
        pump_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        runtime.stream_bus.close_session(session_id)


async def agent_chat_stream(
    *,
    session_id: str,
    question: str,
    contexts: list[str],
    settings: Any,
    user_id: int | None,
    paper_id: str | None,
) -> AsyncIterator[str]:
    """Standard RAG chat via AgentLoop with skill tools."""
    runtime = get_runtime()
    user_settings = await _settings_for_turn(settings, user_id)
    auth = AuthContext(user_id=user_id or 0, paper_id=paper_id)
    context_block = "\n\n".join(contexts[:8])
    system = (
        "You are a scholarly assistant. Answer based on the provided paper context. "
        "Call tools when external or computational help is needed.\n\n"
        f"## Context\n{context_block}"
    )

    selected = runtime.skill_registry.select_tools(
        question,
        top_k=settings.skill_retrieval_top_k,
        min_similarity=settings.skill_similarity_threshold,
    )
    tool_mask = {s.tool_name for s in selected} if selected else {"run_skill"}
    tool_mask.add("run_skill")

    turn = TurnConfig(
        system_prompt=system,
        tool_mask=tool_mask,
        max_iterations=settings.agent_max_tool_rounds,
        user_settings=user_settings,
    )

    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        async for event in runtime.stream_bus.subscribe(session_id):
            await queue.put(event)

    pump_task = asyncio.create_task(pump())
    run_task = asyncio.create_task(runtime.loop.run_streaming(session_id, question, auth, turn_config=turn))

    try:
        while not run_task.done() or not queue.empty():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            if event.event_type == EventType.TEXT and event.data.get("delta"):
                yield _sse_event({"type": "token", "text": event.data.get("text")})
            elif event.event_type == EventType.STAGE and event.data.get("history_unavailable"):
                yield _sse_event(
                    {
                        "type": "stage",
                        "stage": "context",
                        "history_unavailable": True,
                        "message": event.data.get("message"),
                    }
                )
            elif event.event_type == EventType.TOOL_CALL:
                yield _sse_event({"type": "tool", "name": event.data.get("name")})
            elif event.event_type == EventType.DONE:
                yield _sse_event({"type": "done", "answer": event.data.get("answer")})
                break
            elif event.event_type == EventType.ERROR:
                yield _sse_event({"type": "error", "text": event.data.get("message")})
                break
    finally:
        pump_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)
        runtime.stream_bus.close_session(session_id)
