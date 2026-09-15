"""Load per-user API settings from the database."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)

# 与 routers/settings.py 的 PIPELINE_PREF_KEYS 保持一致（避免循环导入，此处独立声明）
PIPELINE_PREF_KEYS = (
    "parser_backend",
    "parser_mineru_enabled",
    "parser_ocr_enabled",
    "formula_backend",
    "translation_provider",
    "vlm_enabled",
    "vlm_model",
    "vlm_max_figures",
    "vlm_mode",
    "grobid_enabled",
    "grobid_base_url",
)


def apply_pipeline_prefs(settings: Any, prefs_json: str | None) -> Any:
    """把用户管线偏好（JSON）覆盖到 settings 副本上；仅白名单键、非空值生效。

    JSON 损坏或键不合法时静默忽略（回退服务端默认），绝不抛异常阻塞管线。
    """
    if not prefs_json:
        return settings
    try:
        prefs = json.loads(prefs_json)
    except (json.JSONDecodeError, TypeError):
        logger.warning("[用户设置] pipeline_prefs JSON 解析失败，忽略用户偏好")
        return settings
    if not isinstance(prefs, dict):
        return settings
    applied = []
    for key, value in prefs.items():
        if key not in PIPELINE_PREF_KEYS or value is None:
            continue
        if not hasattr(settings, key):
            continue
        setattr(settings, key, value)
        applied.append(key)
    if applied:
        logger.info("[用户设置] 管线偏好已生效: %s", applied)
    return settings


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

    # 用户级管线偏好（解析引擎/翻译通道/VLM/GROBID）覆盖到 settings
    apply_pipeline_prefs(user_settings, config.pipeline_prefs)

    return user_settings
