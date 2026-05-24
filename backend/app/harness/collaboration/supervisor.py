"""SupervisorPattern — one agent delegates to sub-agents and merges results."""

from __future__ import annotations

from typing import Any

from .._types import AgentResult, CollaborationResult
from ..events import EventBus
from ...harness.agents.base import BaseAgent
from .base import BaseCollaborationPattern


class SupervisorPattern(BaseCollaborationPattern):
    """Supervisor decomposes a task, dispatches to workers, then merges.

    Flow:
        1. Supervisor receives the task and decomposes it into sub-tasks
        2. Each worker agent handles a sub-task
        3. Supervisor merges all results into a final answer

    Args:
        supervisor: The coordinating agent.
        workers: List of worker agents to dispatch to.
        merge_prompt_template: Prompt template for the merge step.
            Available variables: {sub_results} (formatted sub-results).
    """

    def __init__(
        self,
        supervisor: BaseAgent,
        workers: list[BaseAgent],
        event_bus: EventBus,
        merge_prompt_template: str | None = None,
    ) -> None:
        super().__init__(
            name="supervisor",
            agents=[supervisor, *workers],
            event_bus=event_bus,
        )
        self.merge_prompt_template = merge_prompt_template or (
            "以下是多个子任务的结果，请将它们整合为一份最终报告：\n\n{sub_results}"
        )

    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        self._emit("supervisor_start")

        supervisor = self.agents[0]
        workers = self.agents[1:]

        trace: list[dict[str, Any]] = []

        # Phase 1: Supervisor decomposes the task
        decompose_prompt = (
            f"请将以下任务分解为 {len(workers)} 个子任务，每个子任务一行，不要编号：\n\n{input_text}"
        )
        decompose_result = await supervisor.execute(
            decompose_prompt,
            system_prompt="你是一个任务分解专家。将复杂任务拆分为独立的子任务。",
            **kwargs,
        )
        trace.append({"role": "supervisor", "phase": "decompose", "agent": supervisor.name})

        if decompose_result.error or not decompose_result.content:
            return CollaborationResult(
                error=f"Supervisor decomposition failed: {decompose_result.error}",
                participants=[supervisor.name],
                trace=trace,
            )

        sub_tasks = [
            line.strip()
            for line in str(decompose_result.content).splitlines()
            if line.strip()
        ][:len(workers)]

        # Phase 2: Workers execute sub-tasks in parallel
        self._emit("workers_start", {"worker_count": len(sub_tasks)})
        sub_results: list[AgentResult] = []

        import asyncio
        tasks = []
        for idx, sub_task in enumerate(sub_tasks):
            worker = workers[idx % len(workers)]
            tasks.append(worker.execute(sub_task, **kwargs))
            trace.append({"role": "worker", "phase": "execute", "agent": worker.name, "sub_task": sub_task[:100]})

        sub_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Phase 3: Supervisor merges
        formatted_results = []
        for idx, result in enumerate(sub_results):
            if isinstance(result, Exception):
                formatted_results.append(f"子任务 {idx + 1} 失败: {result}")
            elif result.content:
                formatted_results.append(f"子任务 {idx + 1} 结果:\n{result.content}")
            else:
                formatted_results.append(f"子任务 {idx + 1}: 无结果")

        merge_prompt = self.merge_prompt_template.format(
            sub_results="\n\n---\n\n".join(formatted_results),
        )
        merge_result = await supervisor.execute(
            merge_prompt,
            system_prompt="你是一个结果整合专家。将多份子报告整合为一份连贯的最终报告。",
            **kwargs,
        )
        trace.append({"role": "supervisor", "phase": "merge", "agent": supervisor.name})

        self._emit("supervisor_end")
        return CollaborationResult(
            final_output=merge_result.content,
            participants=[a.name for a in self.agents],
            rounds=1,
            trace=trace,
            error=merge_result.error,
        )
