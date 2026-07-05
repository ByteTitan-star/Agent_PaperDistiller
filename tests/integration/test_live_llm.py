"""Optional live checks — skipped unless DEEPSEEK_API_KEY is configured."""

from __future__ import annotations

import os

import pytest

from app.agent.models import OpenAIGateway

pytestmark = pytest.mark.integration

_SKIP = not os.getenv("DEEPSEEK_API_KEY") or os.getenv("DEEPSEEK_API_KEY") in {"", "your-api-key", "changeme"}


@pytest.mark.asyncio
@pytest.mark.skipif(_SKIP, reason="Set DEEPSEEK_API_KEY to run live LLM integration test")
async def test_openai_gateway_minimal_completion() -> None:
    gateway = OpenAIGateway(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        timeout_sec=30.0,
    )
    response = await gateway.chat_completion(
        [{"role": "user", "content": "Reply with exactly: pong"}],
        temperature=0.0,
        max_tokens=16,
    )
    assert response.content
    assert "pong" in response.content.lower()
