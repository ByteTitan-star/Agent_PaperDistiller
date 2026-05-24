"""HITLStore — file-based persistence for HITL states."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._types import HITLState


class HITLStore:
    """JSON-file backed store for HITL approval states."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or Path("data/hitl")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, hitl_id: str) -> Path:
        return self.data_dir / f"{hitl_id}.json"

    def save(self, state: HITLState) -> None:
        data = {
            "id": state.id,
            "step_name": state.step_name,
            "pipeline_state": state.pipeline_state,
            "status": state.status,
            "feedback": state.feedback,
            "edited_state": state.edited_state,
            "created_at": state.created_at,
            "resolved_at": state.resolved_at,
        }
        self._path(state.id).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self, hitl_id: str) -> HITLState | None:
        path = self._path(hitl_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return HITLState(
            id=data["id"],
            step_name=data["step_name"],
            pipeline_state=data.get("pipeline_state", {}),
            status=data.get("status", "pending"),
            feedback=data.get("feedback"),
            edited_state=data.get("edited_state"),
            created_at=data.get("created_at", ""),
            resolved_at=data.get("resolved_at"),
        )

    def list_by_status(self, status: str) -> list[HITLState]:
        results: list[HITLState] = []
        for path in self.data_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == status:
                    results.append(HITLState(
                        id=data["id"],
                        step_name=data["step_name"],
                        pipeline_state=data.get("pipeline_state", {}),
                        status=data.get("status", "pending"),
                        feedback=data.get("feedback"),
                        edited_state=data.get("edited_state"),
                        created_at=data.get("created_at", ""),
                        resolved_at=data.get("resolved_at"),
                    ))
            except Exception:
                continue
        return results
