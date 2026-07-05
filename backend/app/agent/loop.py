"""AgentLoop — production ReAct driver."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..tools.base import ToolContext
from .context import ContextEngine, WorkingSet
from .errors import (
    LLMToolCallParseError,
    MaxIterationsError,
    SessionCancelledError,
    ToolArgumentError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRepeatedCallError,
)
from .models import LLMGateway, OpenAIGateway
from .registry import ToolRegistry
from .schemas import (
    AuthContext,
    EventType,
    StreamEvent,
    SubAgentHandle,
    ToolCall,
    ToolResultStatus,
    tenant_session_key,
)
from .state import StateStore
from .stream import StreamBus
from .sub_agent_store import SubAgentStore

logger = logging.getLogger(__name__)

HITLCallback = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any] | None]]


@dataclass
class TurnConfig:
    system_prompt: str | None = None
    tool_mask: set[str] | None = None
    max_iterations: int = 12
    temperature: float = 0.7
    deep_search: bool = False
    user_settings: Any | None = None
    hitl_checkpoints: dict[str, HITLCallback] = field(default_factory=dict)


@dataclass
class RuntimeBundle:
    """Composition root shared by API and worker."""

    loop: AgentLoop
    registry: ToolRegistry
    stream_bus: StreamBus
    state_store: StateStore
    context_engine: ContextEngine
    sub_agent_store: SubAgentStore
    sandbox_manager: Any | None = None
    skill_registry: Any | None = None
    storage: Any | None = None
    broker: Any | None = None
    agent_factory: Any | None = None
    collaboration_registry: Any | None = None
    settings: Any | None = None
    pipeline_orchestrator: Any | None = None


class AgentLoop:
    """Drive ReAct until DONE, ERROR, or cancel."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        stream_bus: StreamBus,
        state_store: StateStore,
        context_engine: ContextEngine,
        sub_agent_store: SubAgentStore,
        llm_factory: Callable[[AuthContext], LLMGateway],
        runtime: RuntimeBundle | None = None,
    ) -> None:
        self._registry = registry
        self._stream = stream_bus
        self._state = state_store
        self._context = context_engine
        self._sub_agent_store = sub_agent_store
        self._llm_factory = llm_factory
        self._runtime = runtime
        self._empty_retry_sessions: set[str] = set()
        self._parse_retries: dict[str, int] = {}

    def _resolve_llm(self, auth: AuthContext, cfg: TurnConfig) -> LLMGateway:
        if cfg.user_settings is not None:
            from .llm_factory import make_llm_gateway

            return make_llm_gateway(cfg.user_settings, auth=auth)
        return self._llm_factory(auth)

    async def _maybe_emit_history_warning(
        self,
        session_id: str,
        working_set: WorkingSet,
        *,
        run_id: str | None,
    ) -> None:
        if working_set.history_loaded:
            return
        await self._emit(
            session_id,
            EventType.STAGE,
            {
                "stage": "context",
                "history_unavailable": True,
                "message": working_set.history_error or "Chat history could not be loaded",
            },
            run_id=run_id,
        )

    async def run(
        self,
        session_id: str,
        user_message: str,
        auth: AuthContext,
        *,
        turn_config: TurnConfig | None = None,
        run_id: str | None = None,
        persist: bool = True,
    ) -> None:
        cfg = turn_config or TurnConfig()
        state_key = tenant_session_key(auth.tenant_id, session_id)
        await self._state.clear_cancelled(state_key)
        self._empty_retry_sessions.discard(session_id)
        self._parse_retries.pop(session_id, None)
        self._registry.clear_session(session_id)

        working_set = await self._context.load_for_turn(
            session_id,
            auth,
            system_prompt=cfg.system_prompt,
        )
        await self._maybe_emit_history_warning(session_id, working_set, run_id=run_id)
        working_set.append_user(user_message)
        run_id = run_id or uuid.uuid4().hex[:12]

        thinking_chain: list[dict[str, Any]] = []
        total_prompt = 0
        total_completion = 0

        try:
            answer, thinking_chain, total_prompt, total_completion = await self._drive(
                session_id=session_id,
                auth=auth,
                working_set=working_set,
                cfg=cfg,
                run_id=run_id,
                thinking_chain=thinking_chain,
            )
            await self._emit(
                session_id,
                EventType.DONE,
                {"answer": answer, "thinking_chain": thinking_chain},
                run_id=run_id,
            )
            if persist:
                await self._context.persist_turn(
                    session_id,
                    user_message=user_message,
                    assistant_message=answer,
                    thinking_chain=thinking_chain,
                    token_usage={"prompt": total_prompt, "completion": total_completion},
                    deep_search=cfg.deep_search,
                )
        except SessionCancelledError:
            await self._emit(session_id, EventType.DONE, {"cancelled": True}, run_id=run_id)
        except MaxIterationsError as exc:
            await self._emit(session_id, EventType.ERROR, {"message": str(exc)}, run_id=run_id)
        except Exception as exc:
            logger.exception("AgentLoop.run failed session=%s", session_id)
            await self._emit(session_id, EventType.ERROR, {"message": str(exc)}, run_id=run_id)
        finally:
            await self._registry.close_session(session_id)

    async def resume(
        self,
        session_id: str,
        auth: AuthContext,
        *,
        turn_config: TurnConfig | None = None,
        run_id: str | None = None,
    ) -> None:
        cfg = turn_config or TurnConfig()
        state_key = tenant_session_key(auth.tenant_id, session_id)
        await self._state.clear_cancelled(state_key)
        self._registry.clear_session(session_id)
        working_set = await self._context.load_for_turn(session_id, auth, system_prompt=cfg.system_prompt)
        run_id = run_id or uuid.uuid4().hex[:12]
        thinking_chain: list[dict[str, Any]] = []
        try:
            answer, thinking_chain, _, _ = await self._drive(
                session_id=session_id,
                auth=auth,
                working_set=working_set,
                cfg=cfg,
                run_id=run_id,
                thinking_chain=thinking_chain,
            )
            await self._emit(session_id, EventType.DONE, {"answer": answer}, run_id=run_id)
        except Exception as exc:
            await self._emit(session_id, EventType.ERROR, {"message": str(exc)}, run_id=run_id)
        finally:
            await self._registry.close_session(session_id)

    async def cancel(self, session_id: str, auth: AuthContext) -> None:
        await self._state.mark_cancelled(tenant_session_key(auth.tenant_id, session_id))

    async def spawn_sub_agent(
        self,
        *,
        parent_session_id: str,
        auth: AuthContext,
        task: str,
        system_prompt: str | None = None,
        tool_mask: set[str] | None = None,
        wait: str = "inline",
    ) -> SubAgentHandle:
        child_session_id = f"{parent_session_id}:sub:{uuid.uuid4().hex[:8]}"
        handle = await self._sub_agent_store.create(
            parent_session_id=parent_session_id,
            child_session_id=child_session_id,
            user_id=auth.user_id,
            task_summary=task[:200],
        )
        await self._emit(
            parent_session_id,
            EventType.SUB_AGENT_SPAWN,
            {"handle_id": handle.handle_id, "child_session_id": child_session_id, "task": task},
        )

        async def _run_child() -> None:
            await self._sub_agent_store.mark_running(handle.handle_id)
            child_auth = AuthContext(user_id=auth.user_id, tenant_id=auth.tenant_id, paper_id=auth.paper_id)
            cfg = TurnConfig(system_prompt=system_prompt, tool_mask=tool_mask, max_iterations=8)
            try:
                ws = await self._context.load_for_turn(child_session_id, child_auth, system_prompt=system_prompt)
                ws.append_user(task)
                answer, _, _, _ = await self._drive(
                    session_id=child_session_id,
                    auth=child_auth,
                    working_set=ws,
                    cfg=cfg,
                    run_id=uuid.uuid4().hex[:12],
                    thinking_chain=[],
                )
                await self._sub_agent_store.mark_done(handle.handle_id, answer)
                await self._emit(
                    parent_session_id,
                    EventType.SUB_AGENT_DONE,
                    {"handle_id": handle.handle_id, "result": answer},
                )
            except Exception as exc:
                await self._sub_agent_store.mark_error(handle.handle_id, str(exc))

        if wait == "inline":
            await _run_child()
            return (await self._sub_agent_store.get(handle.handle_id)) or handle
        asyncio.create_task(_run_child())
        return handle

    async def _drive(
        self,
        *,
        session_id: str,
        auth: AuthContext,
        working_set: WorkingSet,
        cfg: TurnConfig,
        run_id: str,
        thinking_chain: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], int, int]:
        llm = self._resolve_llm(auth, cfg)
        tools = self._registry.get_tool_definitions(cfg.tool_mask)
        total_prompt = 0
        total_completion = 0
        state_key = tenant_session_key(auth.tenant_id, session_id)

        for iteration in range(cfg.max_iterations):
            if await self._state.is_cancelled(state_key):
                raise SessionCancelledError("Session cancelled")

            for checkpoint_name, callback in cfg.hitl_checkpoints.items():
                decision = await callback(checkpoint_name, {"iteration": iteration, "session_id": session_id})
                if decision is not None:
                    working_set.append_user(json.dumps(decision, ensure_ascii=False))

            messages = working_set.to_openai_messages()
            response = await llm.chat_completion(messages, tools=tools or None, temperature=cfg.temperature)
            total_prompt += response.prompt_tokens
            total_completion += response.completion_tokens

            if response.content:
                await self._emit(session_id, EventType.TEXT, {"text": response.content}, run_id=run_id)
                thinking_chain.append({"type": "text", "content": response.content})

            if not response.tool_calls:
                if not (response.content or "").strip() and session_id not in self._empty_retry_sessions:
                    self._empty_retry_sessions.add(session_id)
                    working_set.append_user("Please provide a substantive answer or call a tool.")
                    continue
                return response.content, thinking_chain, total_prompt, total_completion

            working_set.append_assistant(response.content, tool_calls=response.tool_calls)
            for tc in response.tool_calls:
                thinking_chain.append(
                    {
                        "type": "tool_call",
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                )
                await self._emit(
                    session_id,
                    EventType.TOOL_CALL,
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                    run_id=run_id,
                )

            batches = self._partition_batches(response.tool_calls)
            for batch in batches:
                if await self._state.is_cancelled(state_key):
                    raise SessionCancelledError("Session cancelled")
                if len(batch) == 1:
                    results = [await self._execute_tool(session_id, auth, batch[0], run_id=run_id)]
                    calls = batch
                else:
                    results = await asyncio.gather(
                        *[self._execute_tool(session_id, auth, tc, run_id=run_id) for tc in batch]
                    )
                    calls = batch
                for tc, result in zip(calls, results, strict=False):
                    working_set.append_tool_result(tc.id, tc.name, result)
                    thinking_chain.append({"type": "tool_result", "name": tc.name, "content": result[:500]})

        raise MaxIterationsError(f"Exceeded max iterations ({cfg.max_iterations})")

    async def _execute_tool(
        self,
        session_id: str,
        auth: AuthContext,
        tc: ToolCall,
        *,
        run_id: str,
    ) -> str:
        ctx = ToolContext(
            session_id=session_id,
            auth=auth,
            call_id=tc.id,
            paper_id=auth.paper_id,
            runtime=self._runtime,
            event_emitter=lambda et, data: self._emit(session_id, et, data, run_id=run_id),
        )
        try:
            result = await self._registry.execute(tc.name, tc.arguments, context=ctx)
            payload = {
                "id": tc.id,
                "name": tc.name,
                "status": result.status.value,
                "content": result.content,
            }
            await self._emit(session_id, EventType.TOOL_RESULT, payload, run_id=run_id)
            return result.content
        except (
            ToolNotFoundError,
            ToolArgumentError,
            ToolRepeatedCallError,
            ToolExecutionError,
        ) as exc:
            msg = str(exc)
            await self._emit(
                session_id,
                EventType.TOOL_RESULT,
                {
                    "id": tc.id,
                    "name": tc.name,
                    "status": ToolResultStatus.ERROR.value,
                    "content": msg,
                },
                run_id=run_id,
            )
            return msg

    def _partition_batches(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        batches: list[list[ToolCall]] = []
        current: list[ToolCall] = []
        for tc in tool_calls:
            tool = self._registry._tools.get(tc.name)
            safe = tool.concurrency_safe if tool else False
            if safe:
                current.append(tc)
            else:
                if current:
                    batches.append(current)
                    current = []
                batches.append([tc])
        if current:
            batches.append(current)
        return batches

    async def _emit(
        self,
        session_id: str,
        event_type: EventType,
        data: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> None:
        await self._stream.emit(StreamEvent(event_type=event_type, session_id=session_id, data=data, run_id=run_id))

    async def run_streaming(
        self,
        session_id: str,
        user_message: str,
        auth: AuthContext,
        *,
        turn_config: TurnConfig | None = None,
        run_id: str | None = None,
    ) -> None:
        """Streaming variant — emits TEXT chunks during LLM generation."""
        cfg = turn_config or TurnConfig()
        state_key = tenant_session_key(auth.tenant_id, session_id)
        await self._state.clear_cancelled(state_key)
        self._registry.clear_session(session_id)
        working_set = await self._context.load_for_turn(session_id, auth, system_prompt=cfg.system_prompt)
        await self._maybe_emit_history_warning(session_id, working_set, run_id=run_id)
        working_set.append_user(user_message)
        run_id = run_id or uuid.uuid4().hex[:12]
        llm = self._resolve_llm(auth, cfg)
        if not isinstance(llm, OpenAIGateway):
            await self.run(session_id, user_message, auth, turn_config=cfg, run_id=run_id)
            return

        tools = self._registry.get_tool_definitions(cfg.tool_mask)
        thinking_chain: list[dict[str, Any]] = []
        total_prompt = 0
        total_completion = 0

        try:
            for _iteration in range(cfg.max_iterations):
                if await self._state.is_cancelled(state_key):
                    raise SessionCancelledError("cancelled")

                messages = working_set.to_openai_messages()
                round_text = ""
                tool_acc: dict[int, dict[str, str]] = {}

                async for chunk in llm.chat_completion_stream(
                    messages, tools=tools or None, temperature=cfg.temperature
                ):
                    if chunk.usage:
                        total_prompt += chunk.usage.get("prompt_tokens", 0)
                        total_completion += chunk.usage.get("completion_tokens", 0)
                    if chunk.text:
                        round_text += chunk.text
                        await self._emit(
                            session_id,
                            EventType.TEXT,
                            {"text": chunk.text, "delta": True},
                            run_id=run_id,
                        )
                    if chunk.thinking:
                        await self._emit(session_id, EventType.THINKING, {"text": chunk.thinking}, run_id=run_id)
                        thinking_chain.append({"type": "thinking", "content": chunk.thinking})
                    if chunk.tool_calls:
                        tool_acc = chunk.tool_calls

                tool_calls: list[ToolCall] = []
                for idx in sorted(tool_acc.keys()):
                    raw = tool_acc[idx]
                    try:
                        args = json.loads(raw.get("arguments") or "{}")
                    except json.JSONDecodeError as exc:
                        raise LLMToolCallParseError(str(exc)) from exc
                    tool_calls.append(
                        ToolCall(
                            id=raw.get("id") or uuid.uuid4().hex[:12],
                            name=raw.get("name") or "",
                            arguments=args if isinstance(args, dict) else {},
                        )
                    )

                if not tool_calls:
                    answer = round_text.strip()
                    thinking_chain.append({"type": "text", "content": answer})
                    await self._emit(
                        session_id,
                        EventType.DONE,
                        {"answer": answer, "thinking_chain": thinking_chain},
                        run_id=run_id,
                    )
                    await self._context.persist_turn(
                        session_id,
                        user_message=user_message,
                        assistant_message=answer,
                        thinking_chain=thinking_chain,
                        token_usage={"prompt": total_prompt, "completion": total_completion},
                        deep_search=cfg.deep_search,
                    )
                    return

                working_set.append_assistant(round_text, tool_calls=tool_calls)
                for tc in tool_calls:
                    thinking_chain.append(
                        {
                            "type": "tool_call",
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    )
                    await self._emit(
                        session_id,
                        EventType.TOOL_CALL,
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                        run_id=run_id,
                    )
                    result = await self._execute_tool(session_id, auth, tc, run_id=run_id)
                    working_set.append_tool_result(tc.id, tc.name, result)

            raise MaxIterationsError(f"Exceeded max iterations ({cfg.max_iterations})")
        except Exception as exc:
            await self._emit(session_id, EventType.ERROR, {"message": str(exc)}, run_id=run_id)
        finally:
            await self._registry.close_session(session_id)
