"""Harness configuration extending the base Settings."""

from __future__ import annotations

from functools import lru_cache

from ..config import Settings as _BaseSettings


class HarnessSettings(_BaseSettings):
    """Extends base Settings with harness-specific fields."""

    # Human-in-the-loop: which pipeline steps require human approval
    hitl_checkpoints: list[str] = []  # e.g. ["critique"]

    # HITL polling interval (seconds) when waiting for human decision
    hitl_poll_interval: float = 2.0

    # Agent default retry count
    agent_retry_count: int = 1
    agent_retry_delay: float = 1.0

    # Collaboration
    default_collaboration_mode: str = "debate"  # debate / supervisor / round_robin

    # Tavily WebSearch
    tavily_api_key: str = ""
    tavily_search_depth: str = "basic"  # basic / advanced
    tavily_max_results: int = 3

    # ReAct Deep Search
    react_max_rounds: int = 5
    react_enable_clarification: bool = True


@lru_cache
def get_harness_settings() -> HarnessSettings:
    return HarnessSettings()
