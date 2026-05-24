"""Tracing — per-step trace spans for pipeline observability."""

from __future__ import annotations

import time
from typing import Any

from .._types import TraceSpan


class Tracer:
    """Creates and manages trace spans for pipeline steps."""

    def __init__(self) -> None:
        self._spans: list[TraceSpan] = []
        self._active: TraceSpan | None = None

    def start_span(self, step_name: str, parent_id: str | None = None) -> TraceSpan:
        span = TraceSpan(
            span_id=f"{step_name}-{len(self._spans)}",
            parent_id=parent_id,
            step_name=step_name,
            start_time=_now_iso(),
            status="pending",
        )
        self._spans.append(span)
        self._active = span
        return span

    def end_span(self, status: str = "ok", metadata: dict[str, Any] | None = None) -> TraceSpan | None:
        if self._active is None:
            return None
        self._active.end_time = _now_iso()
        self._active.status = status
        if metadata:
            self._active.metadata.update(metadata)
        span = self._active
        self._active = None
        return span

    @property
    def spans(self) -> list[TraceSpan]:
        return list(self._spans)

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [
            {
                "span_id": s.span_id,
                "parent_id": s.parent_id,
                "step_name": s.step_name,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "status": s.status,
                "metadata": s.metadata,
            }
            for s in self._spans
        ]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
