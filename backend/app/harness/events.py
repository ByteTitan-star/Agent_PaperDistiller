"""Central event bus for the harness framework."""

from __future__ import annotations

import fnmatch
from typing import Any, Callable

from ._types import HarnessEvent, HookCallback


class EventBus:
    """In-process synchronous event dispatcher.

    Layers emit events; subscribers react. Event patterns support
    glob-style matching (e.g. ``agent.*`` matches ``agent.init``).
    """

    def __init__(self) -> None:
        self._subscribers: list[tuple[str, HookCallback]] = []
        self._global_subscribers: list[HookCallback] = []

    def subscribe(self, event_pattern: str, callback: HookCallback) -> None:
        self._subscribers.append((event_pattern, callback))

    def subscribe_all(self, callback: HookCallback) -> None:
        self._global_subscribers.append(callback)

    def emit(self, event: HarnessEvent) -> None:
        if not event.timestamp:
            from datetime import datetime, timezone
            event.timestamp = datetime.now(timezone.utc).isoformat()

        event_key = f"{event.layer}.{event.action}"

        for pattern, callback in self._subscribers:
            if fnmatch.fnmatch(event_key, pattern) or fnmatch.fnmatch(event.component, pattern):
                try:
                    callback(event)
                except Exception:
                    pass

        for callback in self._global_subscribers:
            try:
                callback(event)
            except Exception:
                pass

    def clear(self) -> None:
        self._subscribers.clear()
        self._global_subscribers.clear()
