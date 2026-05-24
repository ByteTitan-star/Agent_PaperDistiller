"""Harness framework for PaperDistiller multi-agent platform."""

from ._types import (
    AgentResult,
    AgentRole,
    CollaborationResult,
    HarnessEvent,
    LifecyclePhase,
    TokenUsage,
    TraceSpan,
)
from .config import HarnessSettings, get_harness_settings
from .events import EventBus

__all__ = [
    "AgentResult",
    "AgentRole",
    "CollaborationResult",
    "EventBus",
    "HarnessEvent",
    "HarnessSettings",
    "LifecyclePhase",
    "TokenUsage",
    "TraceSpan",
    "get_harness_settings",
]
