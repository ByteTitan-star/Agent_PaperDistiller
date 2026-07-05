"""DeepSeek Agent — 封装 DeepSeek API（OpenAI 兼容格式）调用。

使用 OpenAI SDK 的兼容接口调用 DeepSeek 大模型，
支持单轮对话（_do_run）和多轮对话（_do_run_with_messages）两种模式。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .._types import AgentResult, AgentRole, TokenUsage
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent


class DeepSeekAgent(BaseAgent):
    """基于 DeepSeek 模型的 Agent（通过 OpenAI 兼容 API 调用）。

    在本项目中的角色是 GENERATOR（生成器），主要用于：
    - 论文内容分析
    - 创新方案生成
    - ToT 分支候选生成

    Attributes:
        _client: OpenAI SDK 客户端实例，延迟初始化（首次调用时创建）。
    """

    def __init__(
        self,
        event_bus: EventBus,  # 事件总线
        settings: HarnessSettings,  # 框架配置（含 DeepSeek API 密钥等）
    ) -> None:
        super().__init__(
            name=settings.generation_model_name,  # Agent 名称
            role=AgentRole.GENERATOR,  # 角色为"生成器"
            event_bus=event_bus,
            settings=settings,
        )
        self._client: Any = None  # OpenAI 客户端，延迟创建

    def _ensure_client(self) -> Any:
        """确保 OpenAI 客户端已创建（延迟初始化模式）。

        首次调用时从配置创建客户端，后续调用直接复用。
        使用延迟初始化避免 import 时就要求 API 密钥。

        Returns:
            OpenAI: 已初始化的 OpenAI 客户端实例。
        """
        if self._client is not None:
            return self._client
        from openai import OpenAI

        self._client = OpenAI(
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url.rstrip("/"),
            timeout=self.settings.deepseek_timeout_sec,
        )
        return self._client

    async def _do_run(self, prompt: str, **kwargs: object) -> AgentResult:
        """执行单轮对话：发送 prompt 给 DeepSeek，返回响应内容。

        Args:
            prompt: 用户输入的提示词。
            **kwargs: 可选参数：
                - system_prompt (str): 系统提示词，默认为论文分析专家角色。
                - temperature (float): 生成温度，默认 0.2（低随机性）。
                - max_tokens (int): 最大输出 token 数，默认 1200。

        Returns:
            AgentResult: 包含模型输出内容和 token 用量的结果。
        """
        client = self._ensure_client()
        # 从 kwargs 提取参数，提供默认值
        system_prompt = kwargs.get("system_prompt", "你是一个顶会级学术论文分析专家。")
        temperature = float(kwargs.get("temperature", 0.2))
        max_tokens = int(kwargs.get("max_tokens", 1200))

        # 在线程池中执行同步 API 调用，避免阻塞事件循环
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=self.settings.deepseek_model,
            messages=[
                {"role": "system", "content": str(system_prompt)},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # 提取响应内容
        content = (response.choices[0].message.content or "").strip()
        # 提取 token 用量（如果 API 返回了的话）
        token_usage = None
        if response.usage:
            token_usage = TokenUsage(
                model_name=self.settings.deepseek_model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
        return AgentResult(content=content, token_usage=token_usage)

    async def _do_run_with_messages(
        self,
        messages: list[dict[str, Any]],  # 完整的消息列表（多轮对话）
        *,
        temperature: float = 0.8,  # 生成温度，默认 0.8（较高随机性，适合创意生成）
        max_tokens: int = 900,  # 最大输出 token 数
    ) -> AgentResult:
        """高级 API：接受完整消息列表而非单条 prompt。

        与 _do_run 的区别：
        - _do_run: 只接受一条 prompt，自动构造 system + user 消息
        - _do_run_with_messages: 接受完整的多轮消息列表，灵活度更高

        适用于 ToT 等需要多轮对话、上下文传递的场景。

        Args:
            messages: OpenAI 格式的消息列表，
                如 [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]。
            temperature: 生成温度，0.0（确定性）到 2.0（随机性）。
            max_tokens: 最大输出 token 数。

        Returns:
            AgentResult: 包含模型输出内容和 token 用量的结果。
        """
        client = self._ensure_client()
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=self.settings.deepseek_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = (response.choices[0].message.content or "").strip()
        token_usage = None
        if response.usage:
            token_usage = TokenUsage(
                model_name=self.settings.deepseek_model,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
        return AgentResult(content=content, token_usage=token_usage)
