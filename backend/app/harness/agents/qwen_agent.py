"""Qwen Agent — wraps OpenAI-compatible Qwen/DashScope API calls."""

from __future__ import annotations

import os
from typing import Any

from .._types import AgentResult, AgentRole, TokenUsage
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent


class QwenAgent(BaseAgent):
    """Agent backed by Qwen (DashScope OpenAI-compatible API)."""

    def __init__(self, event_bus: EventBus, settings: HarnessSettings) -> None:
        super().__init__(
            name=settings.evaluation_model_name,
            role=AgentRole.EVALUATOR,
            event_bus=event_bus,
            settings=settings,
        )
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import OpenAI
        api_key = os.getenv("DASHSCOPE_API_KEY", "").strip() or self.settings.qwen_api_key
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.settings.qwen_base_url.rstrip("/"),
            timeout=self.settings.qwen_timeout_sec,
        )
        return self._client

    async def _do_run(self, prompt: str, **kwargs: object) -> AgentResult:
        client = self._ensure_client()
        system_prompt = kwargs.get("system_prompt", "你是客观严谨、以可复现性为核心的审稿人。")
        temperature = float(kwargs.get("temperature", 0.2))
        max_tokens = int(kwargs.get("max_tokens", 900))

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

        content = (response.choices[0].message.content or "").strip()
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
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 900,
    ) -> AgentResult:
        """Advanced API: accept full message list."""
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


import asyncio
