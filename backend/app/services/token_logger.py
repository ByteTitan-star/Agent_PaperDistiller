"""Token usage logging to database."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def log_token_to_db(
    user_id: int | None,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    action_type: str = "chat",
    detail: dict[str, Any] | None = None,
) -> None:
    """Persist a single token usage record to the database."""
    try:
        from ..database import async_session_factory
        from ..models import TokenUsageLog

        async with async_session_factory() as session:
            log = TokenUsageLog(
                user_id=user_id,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                action_type=action_type,
                detail=detail,
            )
            session.add(log)
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to log token usage to DB: %s", exc)
