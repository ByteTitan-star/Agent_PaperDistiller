"""Harness framework shared types and enums."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable


class AgentRole(enum.Enum):
    GENERATOR = "generator"
    EVALUATOR = "evaluator"
    TRANSLATOR = "translator"
    CRITIC = "critic"
    PARSER = "parser"
    SUPERVISOR = "supervisor"


class LifecyclePhase(enum.Enum):
    INIT = "init"
    PRE_RUN = "pre_run"
    POST_RUN = "post_run"
    ON_ERROR = "on_error"
    SHUTDOWN = "shutdown"


@dataclass
class HarnessEvent:
    layer: str       # "agent" / "pipeline" / "tool" / "session" / "hitl" / "collaboration"
    component: str   # component name
    action: str      # "init" / "pre_run" / "post_run" / "error" / ...
    timestamp: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


HookCallback = Callable[[HarnessEvent], None]


@dataclass
class TraceSpan:
    span_id: str
    parent_id: str | None = None
    step_name: str = ""
    start_time: str = ""
    end_time: str = ""
    status: str = "pending"  # pending / ok / error
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUsage:
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timestamp: str = ""

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class AgentResult:
    content: Any = None
    token_usage: TokenUsage | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollaborationResult:
    final_output: Any = None
    participants: list[str] = field(default_factory=list)
    rounds: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
