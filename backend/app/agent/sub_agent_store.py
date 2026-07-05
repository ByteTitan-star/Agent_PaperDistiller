"""Sub-agent handle persistence."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, update

from .schemas import SubAgentHandle

logger = logging.getLogger(__name__)


@dataclass
class InMemorySubAgentStore:
    """Process-local sub-agent store for tests and single-worker dev."""

    _records: dict[str, SubAgentHandle] = field(default_factory=dict)

    async def create(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
        user_id: int,
        task_summary: str,
    ) -> SubAgentHandle:
        handle_id = uuid.uuid4().hex[:16]
        handle = SubAgentHandle(
            handle_id=handle_id,
            parent_session_id=parent_session_id,
            child_session_id=child_session_id,
            status="pending",
            task_summary=task_summary,
        )
        self._records[handle_id] = handle
        return handle

    async def mark_running(self, handle_id: str) -> None:
        rec = self._records.get(handle_id)
        if rec:
            self._records[handle_id] = SubAgentHandle(
                handle_id=rec.handle_id,
                parent_session_id=rec.parent_session_id,
                child_session_id=rec.child_session_id,
                status="running",
                task_summary=rec.task_summary,
                result=rec.result,
                error=rec.error,
            )

    async def mark_done(self, handle_id: str, result: str) -> None:
        rec = self._records.get(handle_id)
        if rec:
            self._records[handle_id] = SubAgentHandle(
                handle_id=rec.handle_id,
                parent_session_id=rec.parent_session_id,
                child_session_id=rec.child_session_id,
                status="done",
                task_summary=rec.task_summary,
                result=result,
                error=rec.error,
            )

    async def mark_error(self, handle_id: str, error: str) -> None:
        rec = self._records.get(handle_id)
        if rec:
            self._records[handle_id] = SubAgentHandle(
                handle_id=rec.handle_id,
                parent_session_id=rec.parent_session_id,
                child_session_id=rec.child_session_id,
                status="error",
                task_summary=rec.task_summary,
                result=rec.result,
                error=error,
            )

    async def get(self, handle_id: str) -> SubAgentHandle | None:
        return self._records.get(handle_id)

    async def list_by_parent(self, parent_session_id: str) -> list[SubAgentHandle]:
        return [rec for rec in self._records.values() if rec.parent_session_id == parent_session_id]


@dataclass
class SubAgentStore:
    async def create(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
        user_id: int,
        task_summary: str,
    ) -> SubAgentHandle:
        handle_id = uuid.uuid4().hex[:16]
        from ..database import async_session_factory
        from ..models import SubAgentRecord

        async with async_session_factory() as db:
            db.add(
                SubAgentRecord(
                    handle_id=handle_id,
                    parent_session_id=parent_session_id,
                    child_session_id=child_session_id,
                    user_id=user_id,
                    status="pending",
                    task_summary=task_summary,
                )
            )
            await db.commit()
        return SubAgentHandle(
            handle_id=handle_id,
            parent_session_id=parent_session_id,
            child_session_id=child_session_id,
            status="pending",
            task_summary=task_summary,
        )

    async def mark_running(self, handle_id: str) -> None:
        await self._set_status(handle_id, "running")

    async def mark_done(self, handle_id: str, result: str) -> None:
        from ..database import async_session_factory
        from ..models import SubAgentRecord

        async with async_session_factory() as db:
            await db.execute(
                update(SubAgentRecord).where(SubAgentRecord.handle_id == handle_id).values(status="done", result=result)
            )
            await db.commit()

    async def mark_error(self, handle_id: str, error: str) -> None:
        from ..database import async_session_factory
        from ..models import SubAgentRecord

        async with async_session_factory() as db:
            await db.execute(
                update(SubAgentRecord).where(SubAgentRecord.handle_id == handle_id).values(status="error", error=error)
            )
            await db.commit()

    async def get(self, handle_id: str) -> SubAgentHandle | None:
        from ..database import async_session_factory
        from ..models import SubAgentRecord

        async with async_session_factory() as db:
            row = await db.execute(select(SubAgentRecord).where(SubAgentRecord.handle_id == handle_id))
            rec = row.scalar_one_or_none()
            if not rec:
                return None
            return SubAgentHandle(
                handle_id=rec.handle_id,
                parent_session_id=rec.parent_session_id,
                child_session_id=rec.child_session_id,
                status=rec.status,
                task_summary=rec.task_summary or "",
                result=rec.result,
                error=rec.error,
            )

    async def list_by_parent(self, parent_session_id: str) -> list[SubAgentHandle]:
        from ..database import async_session_factory
        from ..models import SubAgentRecord

        async with async_session_factory() as db:
            rows = await db.execute(select(SubAgentRecord).where(SubAgentRecord.parent_session_id == parent_session_id))
            return [
                SubAgentHandle(
                    handle_id=r.handle_id,
                    parent_session_id=r.parent_session_id,
                    child_session_id=r.child_session_id,
                    status=r.status,
                    task_summary=r.task_summary or "",
                    result=r.result,
                    error=r.error,
                )
                for r in rows.scalars().all()
            ]

    async def _set_status(self, handle_id: str, status: str) -> None:
        from ..database import async_session_factory
        from ..models import SubAgentRecord

        async with async_session_factory() as db:
            await db.execute(update(SubAgentRecord).where(SubAgentRecord.handle_id == handle_id).values(status=status))
            await db.commit()


@dataclass
class ResilientSubAgentStore:
    """SQL-backed store with automatic in-memory fallback when DB is unavailable."""

    _sql: SubAgentStore = field(default_factory=SubAgentStore)
    _memory: InMemorySubAgentStore = field(default_factory=InMemorySubAgentStore)
    _use_memory: bool = False

    async def create(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
        user_id: int,
        task_summary: str,
    ) -> SubAgentHandle:
        if self._use_memory:
            return await self._memory.create(
                parent_session_id=parent_session_id,
                child_session_id=child_session_id,
                user_id=user_id,
                task_summary=task_summary,
            )
        try:
            return await self._sql.create(
                parent_session_id=parent_session_id,
                child_session_id=child_session_id,
                user_id=user_id,
                task_summary=task_summary,
            )
        except Exception:
            logger.exception("SubAgentStore DB create failed; falling back to in-memory")
            self._use_memory = True
            return await self._memory.create(
                parent_session_id=parent_session_id,
                child_session_id=child_session_id,
                user_id=user_id,
                task_summary=task_summary,
            )

    async def mark_running(self, handle_id: str) -> None:
        store = self._memory if self._use_memory else self._sql
        try:
            await store.mark_running(handle_id)
        except Exception:
            if not self._use_memory:
                self._use_memory = True
                await self._memory.mark_running(handle_id)

    async def mark_done(self, handle_id: str, result: str) -> None:
        store = self._memory if self._use_memory else self._sql
        try:
            await store.mark_done(handle_id, result)
        except Exception:
            if not self._use_memory:
                self._use_memory = True
                await self._memory.mark_done(handle_id, result)

    async def mark_error(self, handle_id: str, error: str) -> None:
        store = self._memory if self._use_memory else self._sql
        try:
            await store.mark_error(handle_id, error)
        except Exception:
            if not self._use_memory:
                self._use_memory = True
                await self._memory.mark_error(handle_id, error)

    async def get(self, handle_id: str) -> SubAgentHandle | None:
        store = self._memory if self._use_memory else self._sql
        try:
            return await store.get(handle_id)
        except Exception:
            if not self._use_memory:
                self._use_memory = True
                return await self._memory.get(handle_id)
            return None

    async def list_by_parent(self, parent_session_id: str) -> list[SubAgentHandle]:
        store = self._memory if self._use_memory else self._sql
        try:
            return await store.list_by_parent(parent_session_id)
        except Exception:
            if not self._use_memory:
                self._use_memory = True
                return await self._memory.list_by_parent(parent_session_id)
            return []


def build_sub_agent_store(
    *,
    use_memory: bool = False,
    auto_fallback: bool = True,
) -> InMemorySubAgentStore | SubAgentStore | ResilientSubAgentStore:
    if use_memory:
        return InMemorySubAgentStore()
    if auto_fallback:
        return ResilientSubAgentStore()
    return SubAgentStore()
