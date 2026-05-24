"""DeepSeek Agent — wraps OpenAI-compatible DeepSeek API calls."""

from __future__ import annotations

from typing import Any

from .._types import AgentResult, AgentRole, TokenUsage
from ..config import HarnessSettings
from ..events import EventBus
from .base import BaseAgent


class DeepSeekAgent(BaseAgent):
    """Agent backed by DeepSeek (OpenAI-compatible API)."""

    def __init__(self, event_bus: EventBus, settings: HarnessSettings) -> None:
        super().__init__(
            name=settings.generation_model_name,
            role=AgentRole.GENERATOR,
            event_bus=event_bus,
            settings=settings,
        )
        self._client: Any = None

    def _ensure_client(self) -> Any:
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
        client = self._ensure_client()
        system_prompt = kwargs.get("system_prompt", "你是一个顶会级学术论文分析专家。")
        temperature = float(kwargs.get("temperature", 0.2))
        max_tokens = int(kwargs.get("max_tokens", 1200))

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

        content = (response.choices[0].message.content or "").strip()
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
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.8,
        max_tokens: int = 900,
    ) -> AgentResult:
        """Advanced API: accept full message list instead of a single prompt."""
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


import asyncio
