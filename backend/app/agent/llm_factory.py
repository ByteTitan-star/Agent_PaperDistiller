"""LLM gateway construction — per-request settings, no global mutation."""

from __future__ import annotations

from typing import Any

from ..config import get_settings
from .models import LLMGateway, OpenAIGateway
from .schemas import AuthContext


def make_llm_gateway(settings: Any, *, auth: AuthContext | None = None) -> LLMGateway:
    """Build an OpenAI-compatible gateway from a settings object."""
    _ = auth  # reserved for per-tenant routing
    return OpenAIGateway(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_sec=settings.deepseek_timeout_sec,
    )


def default_llm_gateway(auth: AuthContext) -> LLMGateway:
    """Fallback gateway using process-wide default settings."""
    return make_llm_gateway(get_settings(), auth=auth)
