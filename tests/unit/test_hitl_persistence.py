"""HITL chat history persistence."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.hitl_persistence import persist_hitl_part


@pytest.mark.asyncio
async def test_persist_hitl_request_writes_system_message() -> None:
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.database.async_session_factory", return_value=mock_cm):
        await persist_hitl_part(
            session_id="sess-99",
            kind="hitl_request",
            hil_id="hil-99",
            hil_event_type="deep_search_pre_search",
            payload={"title": "计划确认", "message": "请确认"},
        )

    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited_once()
    row = mock_db.add.call_args[0][0]
    assert row.session_id == "sess-99"
    assert row.role == "system"
    assert row.deep_search is True
    assert row.contexts["hitl_part"]["kind"] == "hitl_request"
    assert row.contexts["hitl_part"]["hil_id"] == "hil-99"


@pytest.mark.asyncio
async def test_persist_hitl_response_includes_option_and_responder() -> None:
    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("app.database.async_session_factory", return_value=mock_cm):
        await persist_hitl_part(
            session_id="sess-99",
            kind="hitl_response",
            hil_id="hil-99",
            hil_event_type="deep_search_pre_report",
            payload={"response_option_id": "edited"},
            responder_user_id="42",
            response_option_id="edited",
        )

    row = mock_db.add.call_args[0][0]
    part = row.contexts["hitl_part"]
    assert part["kind"] == "hitl_response"
    assert part["response_option_id"] == "edited"
    assert part["responder_user_id"] == "42"
