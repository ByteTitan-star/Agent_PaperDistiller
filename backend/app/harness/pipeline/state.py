"""HarnessPaperState — 扩展 PaperState，增加 harness 框架内部元数据。

在原有流水线状态字段基础上，增加了追踪、事件、Token 统计等
harness 框架专用的内部字段，使用 _harness_ 前缀与原有字段区分。
"""

from __future__ import annotations

from typing import Any, TypedDict


class HarnessPaperState(TypedDict, total=False):
    """扩展 PaperState，增加 harness 内部元数据。

    所有原有字段完整保留；harness 新增字段使用 _harness_ 前缀，
    表明这些是框架内部数据，不应影响已有业务逻辑。

    原有字段（与 PaperState 一致）：
        task_id: 任务 ID
        paper_id: 论文 ID
        title: 论文标题
        target_language: 目标翻译语言
        template_name: 分析模板名称
        generation_model_name: 生成模型名称
        evaluation_model_name: 评估模型名称
        collaboration_mode: 协作模式
        text: 论文全文
        sections: 论文章节列表 [(标题, 内容), ...]
        chunks: 论文分块列表
        translated_sections: 翻译后的章节列表
        translation_failures: 翻译失败次数
        translation_retry_count: 翻译重试次数
        translated_chunks: 翻译后的分块列表
        template_text: 模板文本
        tags: 提取到的标签列表

    Harness 新增字段（_harness_ 前缀）：
        _harness_traces: 追踪跨度列表，记录每个步骤的执行信息
        _harness_events: 事件列表，记录流水线执行过程中产生的所有事件
        _harness_token_total: 累计消耗的 token 总数
        _harness_start_time: 流水线开始执行时间
        _harness_step_timings: 各步骤的耗时统计 {step_name: seconds}
    """

    # ---- 原有 PaperState 字段 ----
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

    # ---- Harness 框架新增的元数据字段 ----
    _harness_traces: list[dict[str, Any]]       # 追踪跨度列表
    _harness_events: list[dict[str, Any]]       # 事件记录列表
    _harness_token_total: int                   # Token 总用量
    _harness_start_time: str                    # 流水线启动时间
    _harness_step_timings: dict[str, float]     # 各步骤耗时 {step_name: seconds}
