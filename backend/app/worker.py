import copy
import logging
import traceback

from .harness.app import get_app_harness
from .harness.config import get_harness_settings

logger = logging.getLogger(__name__)
harness_settings = get_harness_settings()


async def _update_paper_status_db(paper_id: str, status: str, domain_tags: list[str] | None = None) -> None:
    """同步更新 MySQL 中论文状态。"""
    try:
        from .database import async_session_factory
        from .models import Paper
        from sqlalchemy import select

        async with async_session_factory() as session:
            result = await session.execute(select(Paper).where(Paper.paper_id == paper_id))
            paper = result.scalar_one_or_none()
            if paper:
                paper.status = status
                if domain_tags:
                    paper.domain_tags = domain_tags
                await session.commit()
        logger.info("Paper status updated in DB: paper_id=%s status=%s", paper_id, status)
    except Exception as exc:
        logger.error("Failed to update paper status in DB: paper_id=%s error=%s", paper_id, exc)


async def _load_user_settings(user_id: int):
    """从数据库加载用户的 API 配置，返回带用户 key 的 HarnessSettings 副本。"""
    from .auth.crypto import aes_decrypt
    from .database import async_session_factory
    from .models import UserApiConfig
    from sqlalchemy import select

    logger.info("Loading API settings for user_id=%d", user_id)

    async with async_session_factory() as session:
        result = await session.execute(
            select(UserApiConfig).where(UserApiConfig.user_id == user_id)
        )
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

    user_settings = copy.deepcopy(harness_settings)
    if ds_key:
        user_settings.deepseek_api_key = ds_key
        logger.info("DeepSeek API key loaded for user_id=%d", user_id)
    if config.deepseek_base_url:
        user_settings.deepseek_base_url = config.deepseek_base_url
    if qwen_key:
        user_settings.qwen_api_key = qwen_key
        logger.info("Qwen API key loaded for user_id=%d", user_id)
    if config.qwen_base_url:
        user_settings.qwen_base_url = config.qwen_base_url
    if tavily_key:
        user_settings.tavily_api_key = tavily_key
        logger.info("Tavily API key loaded for user_id=%d", user_id)

    return user_settings


async def execute_pipeline(
    task_id: str,
    paper_id: str,
    title: str,
    target_language: str,
    template_name: str,
    user_id: int | None = None,
) -> None:
    logger.info(
        "=== Pipeline started === task_id=%s paper_id=%s title=%s template=%s user_id=%s",
        task_id, paper_id, title, template_name, user_id,
    )

    # 加载用户专属的 API 配置
    if user_id is not None:
        try:
            user_settings = await _load_user_settings(user_id)
            logger.info("User settings loaded successfully for user_id=%d", user_id)
        except ValueError as exc:
            logger.error("User settings load failed: user_id=%d error=%s", user_id, exc)
            from .dependencies import broker
            await broker.update(task_id, "failed", 0, str(exc))
            await _update_paper_status_db(paper_id, "failed")
            return
        except Exception as exc:
            logger.error("Unexpected error loading user settings: user_id=%d error=%s\n%s",
                         user_id, exc, traceback.format_exc())
            from .dependencies import broker
            await broker.update(task_id, "failed", 0, f"加载用户配置失败: {exc}")
            await _update_paper_status_db(paper_id, "failed")
            return
    else:
        user_settings = harness_settings

    # --- 尝试 App Harness（LangGraph）路径 ---
    app_harness = get_app_harness()
    harness_available = app_harness.is_initialized and app_harness.pipeline_harness
    logger.info("App harness status: initialized=%s has_pipeline=%s",
                app_harness.is_initialized, bool(app_harness.pipeline_harness))

    if harness_available:
        try:
            logger.info("Running pipeline via App Harness (LangGraph): task_id=%s", task_id)
            tags = await app_harness.pipeline_harness.run(
                task_id=task_id,
                paper_id=paper_id,
                title=title,
                target_language=target_language,
                template_name=template_name,
                settings=user_settings,
            )
            app_harness.storage.update_paper_status(paper_id, "completed", domain_tags=tags)
            await _update_paper_status_db(paper_id, "completed", domain_tags=tags)
            logger.info("Pipeline completed via App Harness: task_id=%s tags=%s", task_id, tags)
            return
        except Exception as exc:
            logger.warning(
                "App Harness pipeline failed, falling back to linear: task_id=%s error=%s\n%s",
                task_id, exc, traceback.format_exc(),
            )
    else:
        logger.info("App Harness not available, using linear pipeline directly: task_id=%s", task_id)

    # --- Legacy fallback: 线性流水线 ---
    from .pipeline.workflow_graph import run_pipeline
    from .dependencies import broker, storage

    try:
        logger.info("Running linear pipeline: task_id=%s paper_id=%s", task_id, paper_id)
        tags = await run_pipeline(
            task_id=task_id,
            paper_id=paper_id,
            title=title,
            target_language=target_language,
            template_name=template_name,
            storage=storage,
            broker=broker,
            settings=user_settings,
            user_id=user_id,
        )
        storage.update_paper_status(paper_id, "completed", domain_tags=tags)
        await _update_paper_status_db(paper_id, "completed", domain_tags=tags)
        logger.info("Linear pipeline completed: task_id=%s tags=%s", task_id, tags)
    except Exception as exc:
        logger.error(
            "Linear pipeline failed: task_id=%s paper_id=%s error=%s\n%s",
            task_id, paper_id, exc, traceback.format_exc(),
        )
        storage.update_paper_status(paper_id, "failed")
        await _update_paper_status_db(paper_id, "failed")
        await broker.update(task_id, "failed", 100, f"任务失败: {exc}")
