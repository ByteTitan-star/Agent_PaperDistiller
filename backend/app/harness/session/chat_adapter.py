"""ChatAdapter — wraps existing chat_with_paper into managed sessions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from ...schemas import ChatRequest, ChatResponse
from ...storage import Storage
from .._types import HarnessEvent
from ..events import EventBus
from .base import ChatMessage, ChatSession, SessionManager


class ChatAdapter:
    """Wraps ``chat_with_paper`` with session history and event emission."""

    def __init__(
        self,
        session_manager: SessionManager,
        storage: Storage,
        event_bus: EventBus,
    ) -> None:
        self.session_manager = session_manager
        self.storage = storage
        self.event_bus = event_bus

    async def chat(
        self,
        paper_id: str,
        payload: ChatRequest,
        session_id: str | None = None,
    ) -> ChatResponse:
        """Execute a chat turn, recording it in the session."""
        session = self.session_manager.get_or_create(paper_id, session_id)

        # Record user message
        user_msg = ChatMessage(
            role="user",
            content=payload.question,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        session.history.append(user_msg)

        self.event_bus.emit(
            HarnessEvent(layer="session", component="ChatAdapter", action="user_message",
                        payload={"session_id": session.session_id}),
        )

        # Delegate to existing chat service
        from ...services.chat import chat_with_paper
        response = await chat_with_paper(paper_id, payload, self.storage)

        # Record assistant message
        assistant_msg = ChatMessage(
            role="assistant",
            content=response.answer,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        session.history.append(assistant_msg)

        self.event_bus.emit(
            HarnessEvent(layer="session", component="ChatAdapter", action="assistant_message",
                        payload={"session_id": session.session_id}),
        )

        return response
