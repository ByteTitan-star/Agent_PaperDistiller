"""Harness 框架配置模块。

继承项目基础 Settings，扩展了 harness 框架专用的配置项，
包括人机协同（HITL）、Agent 重试策略、多 Agent 协作模式、
Tavily 搜索、ReAct 深度搜索等。
"""

from __future__ import annotations

from functools import lru_cache

from ..config import Settings as _BaseSettings


class HarnessSettings(_BaseSettings):
    """Harness 框架配置，继承基础 Settings 并添加 harness 专用字段。

    Attributes:
        hitl_checkpoints: 需要人工审批的流水线步骤名称列表。
            例如 ["critique"] 表示在 critique 步骤暂停等待人工决策。
        hitl_poll_interval: HITL 等待人工决策时的轮询间隔（秒）。
        agent_retry_count: Agent 调用失败时的默认重试次数。
        agent_retry_delay: Agent 重试之间的等待时间（秒）。
        default_collaboration_mode: 默认的多 Agent 协作模式。
            可选值："debate"（辩论）/ "supervisor"（监督者）/ "round_robin"（轮询）。
        tavily_api_key: Tavily Web 搜索 API 密钥（用于 ReAct 深度搜索）。
        tavily_search_depth: Tavily 搜索深度，"basic"（基础）或 "advanced"（深度）。
        tavily_max_results: Tavily 单次搜索返回的最大结果数。
        react_max_rounds: ReAct 深度搜索的最大推理-搜索循环轮次。
        react_enable_clarification: 是否在 ReAct 搜索前先向用户澄清问题。
    """

    # ---- 人机协同（Human-in-the-Loop）配置 ----
    hitl_checkpoints: list[str] = []  # 需要人工审批的步骤，如 ["critique"]
    hitl_poll_interval: float = 2.0   # 等待人工决策的轮询间隔（秒）

    # ---- Agent 重试配置 ----
    agent_retry_count: int = 1        # 失败重试次数
    agent_retry_delay: float = 1.0    # 重试间隔（秒）

    # ---- 多 Agent 协作配置 ----
    default_collaboration_mode: str = "debate"  # debate / supervisor / round_robin

    # ---- Tavily Web 搜索配置（ReAct 深度搜索使用） ----
    tavily_api_key: str = ""                # API 密钥
    tavily_search_depth: str = "basic"      # 搜索深度：basic / advanced
    tavily_max_results: int = 3             # 单次最大结果数

    # ---- ReAct 深度搜索配置 ----
    react_max_rounds: int = 5               # 最大推理-搜索轮次
    react_enable_clarification: bool = True  # 是否启用问题澄清


@lru_cache
def get_harness_settings() -> HarnessSettings:
    return HarnessSettings()
