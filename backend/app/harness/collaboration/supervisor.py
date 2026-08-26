"""监督者模式（SupervisorPattern）— 一个监督者分解任务，多个工人并行执行，最后合并。

流程：
    1. Supervisor（监督者）接收任务并分解为 N 个子任务
    2. 每个 Worker（工人）并行处理一个子任务
    3. Supervisor 将所有子任务结果合并为最终报告
"""

from __future__ import annotations

from typing import Any

from ...harness.agents.base import BaseAgent
from .._types import AgentResult, CollaborationResult
from ..events import EventBus
from .base import BaseCollaborationPattern


class SupervisorPattern(BaseCollaborationPattern):
    """监督者模式：一个 Agent 负责任务分解和结果合并，多个 Agent 并行执行子任务。

    流程：
        1. Supervisor（监督者，agents[0]）将任务分解为 len(workers) 个子任务
        2. 每个 Worker（agents[1:]）并行执行对应的子任务
        3. Supervisor 使用合并模板将所有子结果整合为最终报告

    Attributes:
        merge_prompt_template: 合并提示词模板，必须包含 {sub_results} 占位符。
    """

    def __init__(
        self,
        supervisor: BaseAgent,  # 监督者 Agent
        workers: list[BaseAgent],  # 工人 Agent 列表
        event_bus: EventBus,  # 事件总线
        merge_prompt_template: str | None = None,  # 自定义合并模板
    ) -> None:
        super().__init__(
            name="supervisor",
            agents=[supervisor, *workers],
            event_bus=event_bus,
        )
        # 默认合并模板
        self.merge_prompt_template = merge_prompt_template or (
            "以下是多个子任务的结果，请将它们整合为一份最终报告：\n\n{sub_results}"
        )

    async def run(self, input_text: str, **kwargs: object) -> CollaborationResult:
        """执行监督者模式流程。

        Args:
            input_text: 初始输入文本。
            **kwargs: 附加参数，传递给所有 Agent。

        Returns:
            CollaborationResult: 包含最终合并结果的协作结果。
        """
        self._emit("supervisor_start")

        supervisor = self.agents[0]  # 监督者
        workers = self.agents[1:]  # 工人们

        trace: list[dict[str, Any]] = []

        # ── 阶段 1：监督者分解任务 ──
        decompose_prompt = f"请将以下任务分解为 {len(workers)} 个子任务，每个子任务一行，不要编号：\n\n{input_text}"
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

        # 按行分割子任务，截取到工人数量
        sub_tasks = [line.strip() for line in str(decompose_result.content).splitlines() if line.strip()][
            : len(workers)
        ]

        # ── 阶段 2：工人并行执行子任务 ──
        self._emit("workers_start", {"worker_count": len(sub_tasks)})
        sub_results: list[AgentResult] = []

        import asyncio

        tasks = []
        for idx, sub_task in enumerate(sub_tasks):
            worker = workers[idx % len(workers)]  # 轮流分配工人
            tasks.append(worker.execute(sub_task, **kwargs))
            trace.append(
                {
                    "role": "worker",
                    "phase": "execute",
                    "agent": worker.name,
                    "sub_task": sub_task[:100],
                }
            )

        # 并行等待所有工人完成（异常会被捕获，不会中断其他工人）
        sub_results = await asyncio.gather(*tasks, return_exceptions=True)

        # ── 阶段 3：监督者合并结果 ──
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
