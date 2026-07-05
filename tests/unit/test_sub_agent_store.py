"""In-memory sub-agent store lifecycle."""

from __future__ import annotations

import pytest

from app.agent.sub_agent_store import InMemorySubAgentStore


@pytest.mark.asyncio
async def test_in_memory_sub_agent_lifecycle() -> None:
    store = InMemorySubAgentStore()
    handle = await store.create(
        parent_session_id="parent",
        child_session_id="parent:sub:abc",
        user_id=1,
        task_summary="research related papers",
    )
    assert handle.status == "pending"

    await store.mark_running(handle.handle_id)
    running = await store.get(handle.handle_id)
    assert running is not None
    assert running.status == "running"

    await store.mark_done(handle.handle_id, "found 3 papers")
    done = await store.get(handle.handle_id)
    assert done is not None
    assert done.status == "done"
    assert done.result == "found 3 papers"

    listed = await store.list_by_parent("parent")
    assert len(listed) == 1
    assert listed[0].handle_id == handle.handle_id
