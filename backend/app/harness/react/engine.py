"""ReActEngine — Reason→Act→Observe loop for deep search."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .._types import AgentResult, HarnessEvent
from ..agents.deepseek_agent import DeepSeekAgent
from ..events import EventBus
from ..hitl._types import HITLDecision
from ..hitl.base import HITLManager
from ..tools.base import HarnessToolRegistry
from .prompts import (
    CLARIFY_SYSTEM,
    CLARIFY_USER,
    FINAL_ANSWER_SYSTEM,
    FINAL_ANSWER_USER,
    REASON_SYSTEM,
    REASON_USER,
)
from .types import ReActPhase, ReActState, ReActStep


class ReActEngine:
    """ReAct (Reason→Act→Observe) engine for deep search.

    Uses DeepSeekAgent for reasoning and HarnessToolRegistry for tool calls.
    Optionally integrates HITL for clarification before searching.
    """

    def __init__(
        self,
        agent: DeepSeekAgent,
        tool_harness: HarnessToolRegistry,
        event_bus: EventBus,
        hitl_manager: HITLManager | None = None,
        max_rounds: int = 5,
    ) -> None:
        self.agent = agent
        self.tool_harness = tool_harness
        self.event_bus = event_bus
        self.hitl_manager = hitl_manager
        self.max_rounds = max_rounds

    async def run(
        self,
        question: str,
        paper_context: list[str],
        clarify: bool = True,
    ) -> ReActState:
        """Execute the full ReAct loop."""
        state = ReActState(
            question=question,
            paper_context=paper_context,
            max_rounds=self.max_rounds,
        )

        self._emit("react_start", {"question": question[:100]})

        # Phase 1: CLARIFY
        if clarify and self.hitl_manager:
            await self._clarify_phase(state)

        # Phase 2-5: ReAct loop
        while state.current_round < state.max_rounds:
            state.current_round += 1

            # REASON
            reasoning_result = await self._reason_phase(state)
            thought = reasoning_result.get("thought", "")
            need_search = reasoning_result.get("need_search", False)
            query = reasoning_result.get("query", "")
            direct_answer = reasoning_result.get("answer", "")

            state.steps.append(ReActStep(phase=ReActPhase.REASON, content=thought))
            state.thinking_chain.append(f"[Round {state.current_round} - 思考] {thought}")
            self._emit("react_reason", {"round": state.current_round, "thought": thought[:100]})

            if not need_search or direct_answer:
                state.final_answer = direct_answer
                break

            # ACT — call tools
            tool_result = await self._act_phase(state, query)
            state.steps.append(ReActStep(
                phase=ReActPhase.ACT,
                content=query,
                tool_name="web_search",
                tool_args={"query": query},
                tool_result=tool_result,
            ))
            self._emit("react_act", {"round": state.current_round, "query": query[:100]})

            # OBSERVE
            observation = self._observe(tool_result)
            state.steps.append(ReActStep(phase=ReActPhase.OBSERVE, content=observation))
            state.thinking_chain.append(
                f"[Round {state.current_round} - 观察] {observation[:200]}"
            )
            self._emit("react_observe", {"round": state.current_round})

        # Phase 6: OUTPUT
        if not state.final_answer:
            state.final_answer = await self._generate_final_answer(state)

        self._emit("react_complete", {
            "rounds": state.current_round,
            "steps": len(state.steps),
        })
        return state

    async def _clarify_phase(self, state: ReActState) -> None:
        """Check if the question needs clarification via HITL."""
        context_summary = "\n".join(state.paper_context[:3])[:2000]
        prompt = CLARIFY_USER.format(
            context=context_summary,
            question=state.question,
        )
        result = await self.agent.execute(
            prompt,
            system_prompt=CLARIFY_SYSTEM,
            temperature=0.1,
            max_tokens=300,
        )
        if result.error or not result.content:
            return

        response = str(result.content).strip()
        if response.upper() == "NO" or len(response) < 10:
            return

        # Needs clarification — HITL interrupt
        if self.hitl_manager and self.hitl_manager.has_checkpoint("react_clarify"):
            hitl_state = await self.hitl_manager.interrupt("react_clarify", {
                "question": state.question,
                "clarification_needed": response,
            })
            decision = await self.hitl_manager.wait_for_decision(hitl_state.id)
            if decision.feedback:
                state.clarification = decision.feedback
                state.thinking_chain.append(f"[澄清] 用户补充：{decision.feedback}")

    async def _reason_phase(self, state: ReActState) -> dict[str, Any]:
        """LLM reasons about current state and decides next action."""
        context_summary = "\n".join(state.paper_context[:5])[:3000]
        search_results = self._format_collected_results(state)
        clarification_section = (
            f"用户补充信息：{state.clarification}\n\n"
            if state.clarification else ""
        )
        tools_desc = self._tools_description()

        system = REASON_SYSTEM.format(tools_description=tools_desc)
        user = REASON_USER.format(
            question=state.question,
            clarification_section=clarification_section,
            context_summary=context_summary,
            search_results=search_results or "（暂无搜索结果）",
            current_round=state.current_round,
            max_rounds=state.max_rounds,
        )

        result = await self.agent.execute(
            user,
            system_prompt=system,
            temperature=0.3,
            max_tokens=800,
        )
        if result.error or not result.content:
            return {"thought": "LLM call failed", "need_search": False, "answer": ""}

        return self._parse_reasoning(str(result.content))

    async def _act_phase(self, state: ReActState, query: str) -> dict[str, Any]:
        """Execute a tool call."""
        return self.tool_harness.execute(
            tool_name="web_search",
            arguments={"query": query, "max_results": 3},
        )

    def _observe(self, tool_result: dict[str, Any]) -> str:
        """Format tool result into an observation string."""
        if "error" in tool_result:
            return f"搜索失败: {tool_result['error']}"

        results = tool_result.get("results", [])
        if not results:
            return "搜索完成但未找到相关结果。"

        parts = []
        for idx, item in enumerate(results[:3], 1):
            title = item.get("title", "无标题")
            content = item.get("content", "")
            url = item.get("url", "")
            parts.append(f"{idx}. [{title}]({url})\n   {content[:300]}")
        return "\n".join(parts)

    async def _generate_final_answer(self, state: ReActState) -> str:
        """Generate the final answer from all collected information."""
        context_summary = "\n".join(state.paper_context[:5])[:3000]
        search_results = self._format_collected_results(state)
        clarification_section = (
            f"用户补充信息：{state.clarification}\n\n"
            if state.clarification else ""
        )
        thinking = "\n".join(state.thinking_chain)

        prompt = FINAL_ANSWER_USER.format(
            question=state.question,
            clarification_section=clarification_section,
            context_summary=context_summary,
            search_results=search_results or "无额外搜索结果",
            thinking=thinking,
        )
        result = await self.agent.execute(
            prompt,
            system_prompt=FINAL_ANSWER_SYSTEM,
            temperature=0.2,
            max_tokens=1500,
        )
        return str(result.content) if result.content else "无法生成最终答案。"

    def _parse_reasoning(self, text: str) -> dict[str, Any]:
        """Parse LLM reasoning output into structured fields."""
        thought_match = re.search(r"THOUGHT:\s*(.+?)(?=\n(?:NEED_SEARCH|ANSWER))", text, re.DOTALL)
        thought = thought_match.group(1).strip() if thought_match else text[:200]

        need_search = "NEED_SEARCH: YES" in text
        query_match = re.search(r"QUERY:\s*(.+)", text)
        query = query_match.group(1).strip() if query_match else ""

        answer_match = re.search(r"ANSWER:\s*(.+)", text, re.DOTALL)
        answer = answer_match.group(1).strip() if answer_match else ""

        if not need_search and not answer:
            answer = text.strip()

        return {
            "thought": thought,
            "need_search": need_search,
            "query": query,
            "answer": answer,
        }

    def _format_collected_results(self, state: ReActState) -> str:
        """Format all collected search results for context."""
        parts: list[str] = []
        for step in state.steps:
            if step.phase == ReActPhase.OBSERVE:
                parts.append(step.content)
        return "\n\n---\n\n".join(parts) if parts else ""

    def _tools_description(self) -> str:
        """Build a description of available tools for the LLM."""
        tools = self.tool_harness.all_tools()
        if not tools:
            return "web_search: 搜索互联网获取最新信息"
        lines = []
        for tool in tools:
            schema = tool.tool_schema
            func = schema.get("function", {})
            name = func.get("name", tool.tool_name)
            desc = func.get("description", tool.description)
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def _emit(self, action: str, payload: dict[str, Any] | None = None) -> None:
        self.event_bus.emit(
            HarnessEvent(layer="react", component="ReActEngine", action=action, payload=payload or {})
        )
