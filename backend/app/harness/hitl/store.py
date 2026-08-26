"""HITLStore — 基于 JSON 文件的人机协同状态持久化存储。

将每个 HITL 审批状态保存为一个独立的 JSON 文件，
支持按状态查询（如查找所有 pending 的待审批项）。
"""

from __future__ import annotations

import json
from pathlib import Path

from ._types import HITLState


class HITLStore:
    """基于 JSON 文件的 HITL 状态存储。

    每个 HITL 审批状态保存为 {hitl_id}.json 文件。
    文件存储在 data/hitl/ 目录下（可配置）。

    Attributes:
        data_dir: 数据存储目录路径。
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        """初始化存储，创建数据目录。

        Args:
            data_dir: 数据存储目录，默认为 data/hitl/。
        """
        self.data_dir = data_dir or Path("data/hitl")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, hitl_id: str) -> Path:
        """根据 HITL ID 获取对应的文件路径。

        Args:
            hitl_id: HITL 状态 ID。

        Returns:
            Path: JSON 文件路径。
        """
        return self.data_dir / f"{hitl_id}.json"

    def save(self, state: HITLState) -> None:
        """保存 HITL 状态到 JSON 文件。

        Args:
            state: 要保存的 HITL 状态对象。
        """
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
        """根据 ID 加载 HITL 状态。

        Args:
            hitl_id: HITL 状态 ID。

        Returns:
            HITLState | None: 对应的状态对象，文件不存在则返回 None。
        """
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
        """按状态查询所有匹配的 HITL 状态。

        遍历 data_dir 下所有 JSON 文件，返回状态匹配的记录。
        单个文件解析失败会被静默跳过。

        Args:
            status: 目标状态，如 "pending" / "approved" / "rejected"。

        Returns:
            list[HITLState]: 匹配的 HITL 状态列表。
        """
        results: list[HITLState] = []
        for path in self.data_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == status:
                    results.append(
                        HITLState(
                            id=data["id"],
                            step_name=data["step_name"],
                            pipeline_state=data.get("pipeline_state", {}),
                            status=data.get("status", "pending"),
                            feedback=data.get("feedback"),
                            edited_state=data.get("edited_state"),
                            created_at=data.get("created_at", ""),
                            resolved_at=data.get("resolved_at"),
                        )
                    )
            except Exception:
                continue  # 静默跳过损坏的文件
        return results
