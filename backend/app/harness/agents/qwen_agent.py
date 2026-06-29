"""Qwen Agent — 封装 Qwen / 通义千问（DashScope OpenAI 兼容格式）调用。

使用 OpenAI SDK 的兼容接口调用 Qwen 大模型，
主要用于评估器角色（EVALUATOR），提供客观严谨的审稿和打分。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from .._types import AgentResult, AgentRole, TokenUsage
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent


class QwenAgent(BaseAgent):
    """基于 Qwen（通义千问）模型的 Agent（通过 DashScope OpenAI 兼容 API 调用）。

    在本项目中的角色是 EVALUATOR（评估器），主要用于：
    - 候选方案的质量评估和打分
    - 论文内容的客观审核
    - ToT 分支候选的评分

    Attributes:
        _client: OpenAI SDK 客户端实例，延迟初始化。
    """

    def __init__(
        self,
        event_bus: EventBus,       # 事件总线
        settings: HarnessSettings,  # 框架配置（含 Qwen API 密钥等）
    ) -> None:
        super().__init__(
            name=settings.evaluation_model_name,  # Agent 名称
            role=AgentRole.EVALUATOR,               # 角色为"评估器"
            event_bus=event_bus,
            settings=settings,
        )
        self._client: Any = None  # OpenAI 客户端，延迟创建

    def _ensure_client(self) -> Any:
        """确保 OpenAI 客户端已创建（延迟初始化模式）。

        API 密钥的优先级：环境变量 DASHSCOPE_API_KEY > 配置文件 qwen_api_key。

        Returns:
            OpenAI: 已初始化的 OpenAI 客户端实例。
        """
        if self._client is not None:
            return self._client
        from openai import OpenAI
        # 优先使用环境变量中的 API 密钥
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip() or self.settings.qwen_api_key
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.settings.qwen_base_url.rstrip("/"),
            timeout=self.settings.qwen_timeout_sec,
        )
        return self._client

    async def _do_run(self, prompt: str, **kwargs: object) -> AgentResult:
        """执行单轮对话：发送 prompt 给 Qwen，返回响应内容。

        Args:
            prompt: 用户输入的提示词。
            **kwargs: 可选参数：
                - system_prompt (str): 系统提示词，默认为审稿人角色。
                - temperature (float): 生成温度，默认 0.2（低随机性，适合评估）。
                - max_tokens (int): 最大输出 token 数，默认 900。

        Returns:
            AgentResult: 包含模型输出内容和 token 用量的结果。
        """
        client = self._ensure_client()
        # 从 kwargs 提取参数，提供默认值
        system_prompt = kwargs.get("system_prompt", "你是客观严谨、以可复现性为核心的审稿人。")
        temperature = float(kwargs.get("temperature", 0.2))
        max_tokens = int(kwargs.get("max_tokens", 900))

        # 在线程池中执行同步 API 调用，避免阻塞事件循环
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=self.settings.qwen_model,
            messages=[
                {"role": "system", "content": str(system_prompt)},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 提取响应内容
        content = (response.choices[0].message.content or "").strip()
        # 提取 token 用量
        token_usage = None
        if response.usage:
            token_usage = TokenUsage(
                model_name=self.settings.qwen_model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
        return AgentResult(content=content, token_usage=token_usage)

    async def _do_run_with_messages(
        self,
        messages: list[dict[str, Any]],  # 完整的消息列表（多轮对话）
        *,
        temperature: float = 0.2,  # 生成温度，评估场景用低值
        max_tokens: int = 900,      # 最大输出 token 数
    ) -> AgentResult:
        """高级 API：接受完整消息列表而非单条 prompt。

        适用于需要多轮对话、上下文传递的评估场景。

        Args:
            messages: OpenAI 格式的消息列表。
            temperature: 生成温度。
            max_tokens: 最大输出 token 数。

        Returns:
            AgentResult: 包含模型输出内容和 token 用量的结果。
        """
        client = self._ensure_client()
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=self.settings.qwen_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = (response.choices[0].message.content or "").strip()
        token_usage = None
        if response.usage:
            token_usage = TokenUsage(
                model_name=self.settings.qwen_model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
        return AgentResult(content=content, token_usage=token_usage)
