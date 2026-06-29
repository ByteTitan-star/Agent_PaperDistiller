"""Harness 框架共享类型与枚举定义。

本模块定义了整个 harness 框架中所有组件共用的数据结构，
包括 Agent 角色、生命周期阶段、事件、追踪跨度、Token 用量、执行结果等。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable


class AgentRole(enum.Enum):
    """Agent 角色枚举，用于标识 Agent 在流水线中的职责。

    Attributes:
        GENERATOR: 生成器角色，负责内容生成（如 DeepSeek）。
        EVALUATOR: 评估器角色，负责质量评估和打分（如 Qwen）。
        TRANSLATOR: 翻译器角色，负责文本翻译。
        CRITIC: 评论者角色，负责多角度批判和审核（如 ToT Agent）。
        PARSER: 解析器角色，负责文档解析和结构化提取。
        SUPERVISOR: 监督者角色，负责任务分解和结果合并。
    """
    GENERATOR = "generator"
    EVALUATOR = "evaluator"
    TRANSLATOR = "translator"
    CRITIC = "critic"
    PARSER = "parser"
    SUPERVISOR = "supervisor"


class LifecyclePhase(enum.Enum):
    """Agent / Pipeline 生命周期阶段枚举。

    用于标识当前执行到哪个阶段，配合事件系统进行监控。

    Attributes:
        INIT: 初始化阶段。
        PRE_RUN: 执行前预处理阶段（可修改 prompt）。
        POST_RUN: 执行后处理阶段（可修改结果）。
        ON_ERROR: 异常处理阶段。
        SHUTDOWN: 关闭/清理阶段。
    """
    INIT = "init"
    PRE_RUN = "pre_run"
    POST_RUN = "post_run"
    ON_ERROR = "on_error"
    SHUTDOWN = "shutdown"


@dataclass
class HarnessEvent:
    """Harness 框架统一事件数据结构。

    所有组件（Agent、Pipeline、Tool、Session、HITL、Collaboration）
    在关键节点都会发射此事件，由 EventBus 分发给订阅者。

    Attributes:
        layer: 事件所属层级，如 "agent" / "pipeline" / "tool" / "session" / "hitl" / "collaboration"。
        component: 产生事件的具体组件名称，如 "DeepSeekAgent" / "PipelineHarness"。
        action: 动作类型，如 "init" / "pre_run" / "post_run" / "error"。
        timestamp: 事件时间戳（UTC ISO 格式），由 EventBus.emit 自动填充。
        payload: 附带数据，不同动作携带不同内容。
    """
    layer: str       # "agent" / "pipeline" / "tool" / "session" / "hitl" / "collaboration"
    component: str   # 组件名称
    action: str      # "init" / "pre_run" / "post_run" / "error" / ...
    timestamp: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


# 事件回调函数类型：接收 HarnessEvent，无返回值
HookCallback = Callable[[HarnessEvent], None]


@dataclass
class TraceSpan:
    """追踪跨度，用于记录 Pipeline 每个步骤的执行信息。

    类似 OpenTelemetry 的 Span 概念，记录一个步骤的开始/结束时间、
    状态和元数据，用于性能分析和问题排查。

    Attributes:
        span_id: 跨度唯一标识，格式为 "{step_name}-{序号}"。
        parent_id: 父跨度 ID，用于构建调用树（目前未使用）。
        step_name: 步骤名称，如 "langgraph_pipeline" / "linear_pipeline"。
        start_time: 开始时间（UTC ISO 格式）。
        end_time: 结束时间（UTC ISO 格式）。
        status: 执行状态，"pending" / "ok" / "error"。
        metadata: 附带元数据，如 tags 列表、错误信息等。
    """
    span_id: str
    parent_id: str | None = None
    step_name: str = ""
    start_time: str = ""
    end_time: str = ""
    status: str = "pending"  # pending / ok / error
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUsage:
    """单次 LLM 调用的 Token 用量统计。

    Attributes:
        model_name: 使用的模型名称，如 "deepseek-chat" / "qwen-plus"。
        prompt_tokens: 输入 prompt 消耗的 token 数。
        completion_tokens: 模型输出消耗的 token 数。
        timestamp: 统计时间戳。
    """
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timestamp: str = ""

    @property
    def total(self) -> int:
        """本次调用消耗的总 token 数（输入 + 输出）。"""
        return self.prompt_tokens + self.completion_tokens


@dataclass
class AgentResult:
    """Agent 单次执行的返回结果。

    Attributes:
        content: Agent 输出的主要内容，可以是字符串、列表、字典等。
        token_usage: 本次调用的 token 用量统计，未调用 LLM 时为 None。
        error: 错误信息，执行成功时为 None。
        metadata: 附带元数据，如协作模式等额外信息。
    """
    content: Any = None
    token_usage: TokenUsage | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollaborationResult:
    """多 Agent 协作的返回结果。

    Attributes:
        final_output: 协作最终输出内容。
        participants: 参与协作的 Agent 名称列表。
        rounds: 协作轮次数。
        trace: 协作过程的详细追踪记录（每一步的参与者、内容预览、错误等）。
        error: 错误信息，协作成功时为 None。
    """
    final_output: Any = None
    participants: list[str] = field(default_factory=list)
    rounds: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
