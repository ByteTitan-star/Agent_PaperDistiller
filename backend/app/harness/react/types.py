"""ReAct engine shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReActPhase(Enum):
    CLARIFY = "clarify"
    REASON = "reason"
    ACT = "act"
    OBSERVE = "observe"
    OUTPUT = "output"


@dataclass
class ReActStep:
    phase: ReActPhase
    content: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None


@dataclass
class ReActState:
    question: str
    clarification: str | None = None
    paper_context: list[str] = field(default_factory=list)
    steps: list[ReActStep] = field(default_factory=list)
    current_round: int = 0
    max_rounds: int = 5
    final_answer: str | None = None
    thinking_chain: list[str] = field(default_factory=list)
