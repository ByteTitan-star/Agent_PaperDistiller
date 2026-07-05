"""Persist HITL request/response parts into chat_messages for replay."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def persist_hitl_part(
    *,
    session_id: str,
    kind: str,
    hil_id: str,
    hil_event_type: str,
    payload: dict[str, Any],
    responder_user_id: str | None = None,
    response_option_id: str | None = None,
) -> None:
    """Write a HITL audit row (system role) into chat_messages."""
    try:
        from ..database import async_session_factory
        from ..models import ChatMessage
    except Exception:
        logger.warning("HITL persistence unavailable (imports failed)", exc_info=True)
        return

    title = payload.get("title") or hil_event_type
    part = {
        "kind": kind,
        "hil_id": hil_id,
        "hil_event_type": hil_event_type,
        "payload": payload,
    }
    if response_option_id is not None:
        part["response_option_id"] = response_option_id
    if responder_user_id is not None:
        part["responder_user_id"] = responder_user_id

    try:
        async with async_session_factory() as db:
            db.add(
                ChatMessage(
                    session_id=session_id,
                    role="system",
                    content=title if kind == "hitl_request" else f"HITL {response_option_id}",
                    contexts={"hitl_part": part},
                    deep_search=True,
                )
            )
            await db.commit()
    except Exception:
        logger.warning("Failed to persist HITL part session=%s hil_id=%s", session_id, hil_id, exc_info=True)
