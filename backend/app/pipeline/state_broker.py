# SSE任务状态管理
import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from .common_utils import utc_now_iso
from ..schemas import TaskState

logger = logging.getLogger(__name__)


def sse_event(payload: dict[str, Any], event: str = "progress") -> str:
    """
    【SSE 事件封装】
    封装 Server-Sent Events (SSE) 文本帧，供 StreamingResponse 持续推送任务状态。

    SSE 格式说明：
    - event: 事件类型标识
    - data: JSON 格式的数据负载
    - 空行表示事件结束

    参数:
        payload: 要发送的数据字典
        event: 事件名称（默认 "progress"）

    返回:
        符合 SSE 规范的格式化字符串
    """
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class TaskBroker:
    """
    【任务状态管理器】
    相当于小型 Redis 消息队列，负责维护任务当前状态，并将状态变更广播给 SSE 订阅者。

    核心功能：
    - 任务状态管理（创建、更新、查询）
    - SSE 订阅管理（支持多个客户端同时订阅）
    - 异步消息广播（状态变更时自动通知订阅者）

    使用场景：
    - 前端通过 SSE 实时获取论文处理进度
    - 支持多任务并行状态追踪
    """

    def __init__(self) -> None:
        """
        【初始化任务代理】
        初始化任务状态表、订阅者表和并发锁。

        属性:
            _tasks: 任务状态字典 {task_id: TaskState}
            _subscribers: 订阅者字典 {task_id: set[asyncio.Queue]}
            _lock: 异步锁，保证并发安全
        """
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
        """
        【创建新任务】
        创建新任务并推送初始状态到所有订阅者。

        初始状态：
        - status: "queued"（已排队）
        - progress: 0（进度 0%）
        - message: "任务已排队，等待执行。"

        参数:
            task_id: 任务唯一标识
            paper_id: 关联的论文 ID
            generation_model_name: 生成模型名称
            evaluation_model_name: 评估模型名称
            collaboration_mode: 协同模式描述
        """
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
        logger.info("Task created: task_id=%s paper_id=%s", task_id, paper_id)
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
        """
        【更新任务状态】
        更新任务状态并通知所有订阅者。

        状态流转示例：
        queued -> parsing -> translating -> summarizing -> critiquing -> done

        参数:
            task_id: 任务 ID
            status: 新状态（如 "parsing", "translating", "done"）
            progress: 进度百分比（0-100）
            message: 状态描述信息
            generation_model_name: 生成模型名称（可选，不传则保持原值）
            evaluation_model_name: 评估模型名称（可选，不传则保持原值）
            collaboration_mode: 协同模式（可选，不传则保持原值）
        """
        async with self._lock:
            if task_id not in self._tasks:
                return
            current = self._tasks[task_id]
            state = TaskState(
                task_id=current.task_id,
                paper_id=current.paper_id,
                status=status,
                progress=progress,
                message=message,
                generation_model_name=generation_model_name or current.generation_model_name,
                evaluation_model_name=evaluation_model_name or current.evaluation_model_name,
                collaboration_mode=collaboration_mode or current.collaboration_mode,
                updated_at=utc_now_iso(),
            )
            self._tasks[task_id] = state
        logger.info(
            "Task updated: task_id=%s status=%s progress=%d%% msg=%s",
            task_id, status, progress, message,
        )
        await self._notify(task_id, state.model_dump())

    async def get(self, task_id: str) -> TaskState | None:
        """
        【获取任务状态】
        获取任务当前状态快照。
        """
        async with self._lock:
            return self._tasks.get(task_id)

    async def cancel(self, task_id: str) -> bool:
        """标记任务为 cancelled，通知订阅者。返回是否成功取消。"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task or task.status in {"done", "failed", "cancelled"}:
                return False
            state = TaskState(
                task_id=task.task_id,
                paper_id=task.paper_id,
                status="cancelled",
                progress=task.progress,
                message="任务已取消。",
                generation_model_name=task.generation_model_name,
                evaluation_model_name=task.evaluation_model_name,
                collaboration_mode=task.collaboration_mode,
                updated_at=utc_now_iso(),
            )
            self._tasks[task_id] = state
        await self._notify(task_id, state.model_dump())
        return True

    async def _notify(self, task_id: str, payload: dict[str, Any]) -> None:
        """
        【内部方法】通知订阅者
        将状态 payload 投递给当前任务的所有订阅队列。

        参数:
            task_id: 任务 ID
            payload: 状态数据字典
        """
        for queue in list(self._subscribers[task_id]):
            await queue.put(payload)

    async def subscribe(self, task_id: str):
        """
        【订阅任务状态流】
        订阅指定任务的状态变更事件流。

        行为说明：
        - 首帧发送当前状态（如果任务存在）
        - 持续发送 progress 事件
        - 空闲时发送 keep-alive 防止连接断开
        - 任务结束（done/failed）后自动结束流

        参数:
            task_id: 要订阅的任务 ID

        返回:
            异步生成器，产生 SSE 格式字符串
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