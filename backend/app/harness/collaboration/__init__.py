"""Multi-agent collaboration patterns."""

from .base import BaseCollaborationPattern
from .debate import DebatePattern
from .registry import CollaborationRegistry
from .round_robin import RoundRobinPattern
from .supervisor import SupervisorPattern

__all__ = [
    "BaseCollaborationPattern",
    "CollaborationRegistry",
    "DebatePattern",
    "RoundRobinPattern",
    "SupervisorPattern",
]
