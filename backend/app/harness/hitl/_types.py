"""HITL shared types — avoids circular imports between base and store."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class HITLState:
    id: str
    step_name: str
    pipeline_state: dict[str, Any]
    status: str = "pending"  # pending / approved / rejected / edited
    feedback: str | None = None
    edited_state: dict[str, Any] | None = None
    created_at: str = ""
    resolved_at: str | None = None


@dataclass
class HITLDecision:
    action: Literal["approved", "rejected", "edited"]
    feedback: str | None = None
    edited_state: dict[str, Any] | None = None


@dataclass
class HITLCheckpoint:
    step_name: str
    description: str = ""
    allow_edit: bool = True
