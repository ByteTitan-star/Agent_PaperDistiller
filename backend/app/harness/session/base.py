"""SessionManager and ChatSession — managed conversation with context."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .._types import HarnessEvent
from ..events import EventBus
from ..config import HarnessSettings


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant"
    content: str
    timestamp: str = ""


@dataclass
class ChatSession:
    session_id: str
    paper_id: str
    history: list[ChatMessage] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """Manages per-paper chat sessions with history tracking."""

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self._sessions: dict[str, ChatSession] = {}

    def create_session(self, paper_id: str) -> ChatSession:
        from datetime import datetime, timezone
        session = ChatSession(
            session_id=uuid4().hex,
            paper_id=paper_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[session.session_id] = session
        self.event_bus.emit(
            HarnessEvent(layer="session", component="SessionManager", action="created",
                        payload={"session_id": session.session_id, "paper_id": paper_id}),
        )
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    def get_or_create(self, paper_id: str, session_id: str | None = None) -> ChatSession:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        return self.create_session(paper_id)

    def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            self.event_bus.emit(
                HarnessEvent(layer="session", component="SessionManager", action="closed",
                            payload={"session_id": session_id}),
            )

    def list_sessions(self, paper_id: str | None = None) -> list[ChatSession]:
        sessions = list(self._sessions.values())
        if paper_id:
            sessions = [s for s in sessions if s.paper_id == paper_id]
        return sessions
