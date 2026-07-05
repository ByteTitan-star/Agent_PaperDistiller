"""Load per-user API settings from the database."""

from __future__ import annotations

import copy
import logging
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def load_user_settings(user_id: int, *, fallback: Any) -> Any:
    """Return a settings copy with the user's decrypted API keys.

    Raises ValueError when the user has no usable API configuration.
    """
    from ..auth.crypto import aes_decrypt
    from ..database import async_session_factory
    from ..models import UserApiConfig

    logger.info("Loading API settings for user_id=%d", user_id)

    async with async_session_factory() as session:
        result = await session.execute(select(UserApiConfig).where(UserApiConfig.user_id == user_id))
        config = result.scalar_one_or_none()

    if config is None:
        logger.warning("No API config found for user_id=%d", user_id)
        raise ValueError("请先在设置页面配置 API Key 后再使用。")

    def _decrypt(val: str | None) -> str | None:
        if not val:
            return None
        try:
            return aes_decrypt(val)
        except Exception:
            return val

    ds_key = _decrypt(config.deepseek_api_key)
    qwen_key = _decrypt(config.qwen_api_key)
    tavily_key = _decrypt(config.tavily_api_key)

    if not ds_key and not qwen_key:
        logger.warning("Neither DeepSeek nor Qwen API key configured for user_id=%d", user_id)
        raise ValueError("请先在设置页面配置 DeepSeek 或 Qwen 的 API Key。")

    user_settings = copy.deepcopy(fallback)
    if ds_key:
        user_settings.deepseek_api_key = ds_key
    if config.deepseek_base_url:
        user_settings.deepseek_base_url = config.deepseek_base_url
    if qwen_key:
        user_settings.qwen_api_key = qwen_key
    if config.qwen_base_url:
        user_settings.qwen_base_url = config.qwen_base_url
    if tavily_key:
        user_settings.tavily_api_key = tavily_key

    return user_settings
