"""HarnessPaperState — extends PaperState with harness metadata."""

from __future__ import annotations

from typing import Any, TypedDict


class HarnessPaperState(TypedDict, total=False):
    """Extends the existing PaperState with harness-internal metadata.

    All original fields are preserved; harness fields use the ``_harness_``
    prefix to signal they are internal and should not affect existing code.
    """

    # --- original PaperState fields ---
    task_id: str
    paper_id: str
    title: str
    target_language: str
    template_name: str
    generation_model_name: str
    evaluation_model_name: str
    collaboration_mode: str
    text: str
    sections: list[tuple[str, str]]
    chunks: list[str]
    translated_sections: list[tuple[str, str]]
    translation_failures: int
    translation_retry_count: int
    translated_chunks: list[str]
    template_text: str
    tags: list[str]

    # --- harness metadata (new) ---
    _harness_traces: list[dict[str, Any]]
    _harness_events: list[dict[str, Any]]
    _harness_token_total: int
    _harness_start_time: str
    _harness_step_timings: dict[str, float]
