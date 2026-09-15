"""document_jobs 管线阶段状态机：UPLOADED → PARSING → CHUNKING → EMBEDDING → INDEXED，任一阶段可转 FAILED。

定位：与 TaskRecord（用户可见的粗粒度进度）互补，记录数据链路的分阶段状态——
能回答"这份文件处理到哪一步 / 卡在哪 / 用的哪个解析引擎"。

约束：
- 线性前向流转（不允许回退，重复转移到当前阶段幂等忽略）；
- 任何阶段可转 FAILED（终态，携带 error 说明）；
- 所有 DB 操作 best-effort：异常只记 warning 返回 False，绝不阻塞主管线。
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)

# 线性阶段顺序（含初始态）
STAGE_ORDER: tuple[str, ...] = ("UPLOADED", "PARSING", "CHUNKING", "EMBEDDING", "INDEXED")
FAILED = "FAILED"
VALID_STAGES = frozenset(STAGE_ORDER) | {FAILED}


def validate_transition(current: str | None, new: str) -> bool:
    """校验阶段流转：线性前向、同阶段幂等、任意阶段可转 FAILED；FAILED 为终态。"""
    if new not in VALID_STAGES:
        return False
    if new == FAILED:
        return current != FAILED  # FAILED 是终态，不可再次转移
    if current is None:
        return new == "UPLOADED"
    if current == FAILED:
        return False
    if current == new:
        return True  # 幂等：重复上报当前阶段忽略
    return STAGE_ORDER.index(new) == STAGE_ORDER.index(current) + 1  # 严格相邻，禁止跳阶段


def _history_entry(stage: str, parser: str | None, error: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"stage": stage, "at": dt.datetime.now(dt.UTC).isoformat()}
    if parser:
        entry["parser"] = parser
    if error:
        entry["error"] = error[:500]
    return entry


async def create_document_job(
    paper_id: str,
    task_id: str,
    *,
    session_factory: Any = None,
) -> bool:
    """创建 UPLOADED 状态的 job 记录（已存在则跳过）。best-effort。"""
    factory = session_factory or _default_session_factory()
    if factory is None:
        return False
    try:
        from ..models import DocumentJob

        async with factory() as session:
            result = await session.execute(select(DocumentJob).where(DocumentJob.paper_id == paper_id))
            if result.scalar_one_or_none() is not None:
                return True
            session.add(
                DocumentJob(
                    paper_id=paper_id,
                    task_id=task_id,
                    stage="UPLOADED",
                    stage_history=[_history_entry("UPLOADED", None, None)],
                )
            )
            await session.commit()
        return True
    except Exception as exc:
        logger.warning("[DocumentJob] 创建失败（忽略，不阻塞管线）: %s", exc)
        return False


async def transition_document_job(
    paper_id: str,
    task_id: str,
    stage: str,
    *,
    parser: str | None = None,
    error: str | None = None,
    session_factory: Any = None,
) -> bool:
    """流转到指定阶段（校验合法性，追加历史）。best-effort：任何失败只告警。"""
    factory = session_factory or _default_session_factory()
    if factory is None:
        return False
    try:
        from ..models import DocumentJob

        async with factory() as session:
            result = await session.execute(select(DocumentJob).where(DocumentJob.paper_id == paper_id))
            job = result.scalar_one_or_none()
            if job is None:
                job = DocumentJob(
                    paper_id=paper_id,
                    task_id=task_id,
                    stage="UPLOADED",
                    stage_history=[_history_entry("UPLOADED", None, None)],
                )
                session.add(job)
                await session.flush()

            if not validate_transition(job.stage, stage):
                logger.warning("[DocumentJob] 非法流转被拒绝 | paper_id=%s %s -> %s", paper_id, job.stage, stage)
                return False

            job.stage = stage
            if parser:
                job.parser = parser
            if error:
                job.error = error[:500]
            history = list(job.stage_history or [])
            history.append(_history_entry(stage, parser, error))
            job.stage_history = history
            await session.commit()
        return True
    except Exception as exc:
        logger.warning("[DocumentJob] 流转失败（忽略，不阻塞管线）: %s", exc)
        return False


async def get_document_job(paper_id: str, *, session_factory: Any = None) -> Any | None:
    """查询 job 当前状态（None = 无记录或 DB 不可用）。"""
    factory = session_factory or _default_session_factory()
    if factory is None:
        return None
    try:
        from ..models import DocumentJob

        async with factory() as session:
            result = await session.execute(select(DocumentJob).where(DocumentJob.paper_id == paper_id))
            return result.scalar_one_or_none()
    except Exception as exc:
        logger.warning("[DocumentJob] 查询失败: %s", exc)
        return None


def _default_session_factory() -> Any:
    try:
        from ..database import async_session_factory

        return async_session_factory
    except Exception:
        return None


__all__ = [
    "FAILED",
    "STAGE_ORDER",
    "VALID_STAGES",
    "create_document_job",
    "get_document_job",
    "transition_document_job",
    "validate_transition",
]
