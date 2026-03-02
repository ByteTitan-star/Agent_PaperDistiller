import asyncio
import json
from collections import defaultdict
from typing import Any

from .common_utils import utc_now_iso
from .schemas import TaskState


def sse_event(payload: dict[str, Any], event: str = "progress") -> str:
    """封装 SSE 文本帧，供 `StreamingResponse` 持续推送任务状态。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class TaskBroker:
    """任务状态管理器。相当于小型 Redis 消息队列。

    负责维护任务当前状态，并将状态变更广播给 SSE 订阅者。
    """

    def __init__(self) -> None:
        """初始化任务状态表、订阅者表和并发锁。"""
        self._tasks: dict[str, TaskState] = {}
        self._subscribers: defaultdict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def create(
        self,
        task_id: str,
        paper_id: str,
        generation_model_name: str | None = None,
        evaluation_model_name: str | None = None,
        collaboration_mode: str | None = None,
    ) -> None:
        """创建新任务并推送初始状态。"""
        state = TaskState(
            task_id=task_id,
            paper_id=paper_id,
            status="queued",
            progress=0,
            message="任务已排队，等待执行。",
            generation_model_name=generation_model_name,
            evaluation_model_name=evaluation_model_name,
            collaboration_mode=collaboration_mode,
            updated_at=utc_now_iso(),
        )
        async with self._lock:
            self._tasks[task_id] = state
        await self._notify(task_id, state.model_dump())

    async def update(
        self,
        task_id: str,
        status: str,
        progress: int,
        message: str,
        generation_model_name: str | None = None,
        evaluation_model_name: str | None = None,
        collaboration_mode: str | None = None,
    ) -> None:
        """更新任务状态并通知所有订阅者。"""
        async with self._lock:
            if task_id not in self._tasks:
                return
            current = self._tasks[task_id]
            state = TaskState(
                task_id=current.task_id,
                paper_id=current.paper_id,
                status=status,  # type: ignore[arg-type]
                progress=progress,
                message=message,
                generation_model_name=generation_model_name or current.generation_model_name,
                evaluation_model_name=evaluation_model_name or current.evaluation_model_name,
                collaboration_mode=collaboration_mode or current.collaboration_mode,
                updated_at=utc_now_iso(),
            )
            self._tasks[task_id] = state
        await self._notify(task_id, state.model_dump())

    async def get(self, task_id: str) -> TaskState | None:
        """获取任务当前状态快照。"""
        async with self._lock:
            return self._tasks.get(task_id)

    async def _notify(self, task_id: str, payload: dict[str, Any]) -> None:
        """将状态 payload 投递给当前任务的所有订阅队列。"""
        for queue in list(self._subscribers[task_id]):
            await queue.put(payload)

    async def subscribe(self, task_id: str):
        """订阅任务状态流。

        行为:
            - 首帧发送当前状态（如果存在）；
            - 持续发送 `progress` 事件；
            - 空闲时发送 keep-alive 防止连接断开；
            - 任务结束（done/failed）后结束流。
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers[task_id].add(queue)

        current = await self.get(task_id)
        if current:
            await queue.put(current.model_dump())

        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield sse_event(payload)
                    if payload.get("status") in {"done", "failed"}:
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            self._subscribers[task_id].discard(queue)


__all__ = ["TaskBroker", "sse_event"]

