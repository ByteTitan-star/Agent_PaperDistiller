"""Context engine — SQL-backed message history and working-set assembly."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from .schemas import AuthContext, LLMMessage, ToolCall

logger = logging.getLogger(__name__)


@dataclass
class WorkingSet:
    """Append-only in-memory turn state."""

    messages: list[LLMMessage] = field(default_factory=list)
    system_prompt: str = ""
    thinking_trace: list[dict[str, Any]] = field(default_factory=list)
    history_loaded: bool = True
    history_error: str | None = None

    def append_user(self, content: str) -> None:
        self.messages.append(LLMMessage(role="user", content=content))

    def append_assistant(
        self,
        content: str,
        *,
        tool_calls: list[ToolCall] | None = None,
        thinking: str = "",
    ) -> None:
        self.messages.append(LLMMessage(role="assistant", content=content, tool_calls=tool_calls))
        if thinking:
            self.thinking_trace.append({"type": "thinking", "content": thinking})

    def append_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.messages.append(LLMMessage(role="tool", content=content, tool_call_id=tool_call_id, name=name))
        self.thinking_trace.append({"type": "tool_result", "name": name, "content": content[:500]})

    def to_openai_messages(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self.system_prompt:
            out.append({"role": "system", "content": self.system_prompt})
        for msg in self.messages:
            if msg.role == "assistant" and msg.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
            elif msg.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": msg.content or "",
                    }
                )
            else:
                out.append({"role": msg.role, "content": msg.content or ""})
        return out


class ContextEngine:
    """Load persisted chat history and persist turn results."""

    def __init__(self, *, default_system_prompt: str = "") -> None:
        self.default_system_prompt = default_system_prompt

    async def load_for_turn(
        self,
        session_id: str,
        auth: AuthContext,
        *,
        system_prompt: str | None = None,
        history_limit: int = 40,
    ) -> WorkingSet:
        ws = WorkingSet(system_prompt=system_prompt or self.default_system_prompt)
        try:
            from ..database import async_session_factory
            from ..models import ChatMessage

            async with async_session_factory() as db:
                rows = await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(history_limit)
                )
                messages = list(reversed(rows.scalars().all()))
                for row in messages:
                    if row.role == "user":
                        ws.append_user(row.content)
                    elif row.role == "assistant":
                        chain = row.thinking_chain or []
                        tool_calls = _tool_calls_from_chain(chain)
                        ws.append_assistant(row.content, tool_calls=tool_calls or None)
        except Exception as exc:
            ws.history_loaded = False
            ws.history_error = str(exc)
            logger.exception("ContextEngine.load_for_turn failed session=%s", session_id)
        return ws

    async def persist_turn(
        self,
        session_id: str,
        *,
        user_message: str,
        assistant_message: str,
        thinking_chain: list[dict[str, Any]] | None = None,
        token_usage: dict[str, int] | None = None,
        deep_search: bool = False,
    ) -> bool:
        try:
            from ..database import async_session_factory
            from ..models import ChatMessage

            async with async_session_factory() as db:
                db.add(
                    ChatMessage(
                        session_id=session_id,
                        role="user",
                        content=user_message,
                        deep_search=deep_search,
                    )
                )
                db.add(
                    ChatMessage(
                        session_id=session_id,
                        role="assistant",
                        content=assistant_message,
                        thinking_chain=thinking_chain,
                        token_usage=token_usage,
                        deep_search=deep_search,
                    )
                )
                await db.commit()
            return True
        except Exception:
            logger.exception("ContextEngine.persist_turn failed session=%s", session_id)
            return False


def _tool_calls_from_chain(chain: list[Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for item in chain:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_call":
            calls.append(
                ToolCall(
                    id=item.get("id") or "",
                    name=item.get("name") or "",
                    arguments=item.get("arguments") or {},
                )
            )
    return calls
