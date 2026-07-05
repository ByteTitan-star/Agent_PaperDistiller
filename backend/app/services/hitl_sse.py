"""Map HITL wire envelopes to SSE (bioagent biomap.hil-compatible subset)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

DEFAULT_HITL_OPTIONS: list[dict[str, str]] = [
    {"id": "approved", "label": "批准继续", "consequence": "按当前计划继续执行"},
    {"id": "rejected", "label": "取消", "consequence": "终止当前流程"},
    {"id": "edited", "label": "修改后继续", "consequence": "使用修改后的参数继续"},
]

HITL_TTL_SECONDS = 3600


def build_hitl_request_envelope(
    *,
    hil_id: str,
    hil_event_type: str,
    session_id: str,
    payload: dict[str, Any],
    streaming: bool = False,
    title: str = "",
    message: str = "",
    options: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "hil_id": hil_id,
        "hil_event_type": hil_event_type,
        "session_id": session_id,
        "at": now,
        "ttl_seconds": HITL_TTL_SECONDS,
        "streaming": streaming,
        "title": title,
        "message": message,
        "payload": payload,
        "options": options or list(DEFAULT_HITL_OPTIONS),
        # biomap.hil CUSTOM wrapper hint for future AG-UI bridge
        "biomap_hil": {
            "event": "hil_request",
            "data": {
                "hil_id": hil_id,
                "hil_event_type": hil_event_type,
                "session_id": session_id,
                "at": now,
                "ttl_seconds": HITL_TTL_SECONDS,
                "payload": payload,
                "options": options or list(DEFAULT_HITL_OPTIONS),
                "streaming": streaming,
            },
        },
    }


def hitl_request_to_sse(envelope: dict[str, Any]) -> str:
    body = {
        "type": "hitl_request",
        **envelope,
        # Legacy alias — remove after frontend fully migrates
        "type_legacy": "hitl_approval",
        "checkpoint": envelope.get("hil_event_type", "").replace("deep_search_", ""),
        "hitl_id": envelope["hil_id"],
        "current_state": envelope.get("payload"),
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def hitl_preview_to_sse(*, checkpoint: str, answer_preview: str, source_count: int) -> str:
    body = {
        "type": "hitl_preview",
        "checkpoint": checkpoint,
        "answer_preview": answer_preview,
        "source_count": source_count,
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
