"""HITL（Human-in-the-Loop）共享类型定义 — 避免模块间循环导入。

定义了人机协同场景下的三个核心数据结构：
- HITLState: 待审批的状态快照
- HITLDecision: 人工做出的决策
- HITLCheckpoint: 审批检查点配置
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class HITLState:
    """待人工审批的状态快照。

    当流水线执行到需要人工介入的检查点时，会将当前状态保存为此对象，
    等待外部系统（API / UI）调用 decide() 提交人工决策。

    Attributes:
        id: 唯一标识（UUID hex），由 HITLManager 自动生成。
        step_name: 触发检查点的步骤名称，如 "pipeline_start" / "critique"。
        pipeline_state: 触发时的流水线状态快照（用于人工查看上下文）。
        status: 当前状态，"pending"（等待中）/ "approved"（已批准）/
                "rejected"（已拒绝）/ "edited"（已修改批准）。
        feedback: 人工反馈文本（可选）。
        edited_state: 人工修改后的状态（当 action="edited" 时使用）。
        created_at: 创建时间（UTC ISO 格式）。
        resolved_at: 审批时间（UTC ISO 格式），未审批时为 None。
    """
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
    """人工做出的决策。

    由外部系统（API / UI）构造，传递给 HITLManager.decide()。

    Attributes:
        action: 决策类型。
            - "approved": 批准继续执行。
            - "rejected": 拒绝，中止流水线。
            - "edited": 修改状态后批准（edited_state 中携带修改内容）。
        feedback: 人工反馈意见（可选）。
        edited_state: 人工修改后的流水线状态（仅 action="edited" 时使用）。
    """
    action: Literal["approved", "rejected", "edited"]
    feedback: str | None = None
    edited_state: dict[str, Any] | None = None


@dataclass
class HITLCheckpoint:
    """审批检查点配置。

    定义在哪个流水线步骤需要暂停等待人工审批。

    Attributes:
        step_name: 步骤名称，需与流水线中的步骤名匹配。
        description: 检查点描述（用于 UI 展示）。
        allow_edit: 是否允许人工修改状态后再批准，默认 True。
    """
    step_name: str
    description: str = ""
    allow_edit: bool = True
