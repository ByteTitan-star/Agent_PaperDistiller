"""Agent runtime wire types — events, auth, tool results, LLM messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_INTENT = "tool_intent"
    DONE = "done"
    ERROR = "error"
    HITL_REQUEST = "hitl_request"
    HITL_RESPONSE = "hitl_response"
    SUB_AGENT_SPAWN = "sub_agent_spawn"
    SUB_AGENT_DONE = "sub_agent_done"
    STAGE = "stage"


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AuthContext:
    user_id: int
    tenant_id: str = "default"
    paper_id: str | None = None


@dataclass(frozen=True, slots=True)
class StreamEvent:
    event_type: EventType
    session_id: str
    data: dict[str, Any]
    run_id: str | None = None
    seq: int | None = None


@dataclass
class ToolResult:
    status: ToolResultStatus
    content: str
    metadata: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMMessage:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class SubAgentHandle:
    handle_id: str
    parent_session_id: str
    child_session_id: str
    status: str  # pending | running | done | error | cancelled
    task_summary: str = ""
    result: str | None = None
    error: str | None = None


def tenant_session_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:{session_id}"
