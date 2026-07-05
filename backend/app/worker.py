import logging
import traceback

from .dependencies import get_app_harness
from .harness.config import get_harness_settings
from .services.user_settings import load_user_settings

logger = logging.getLogger(__name__)
harness_settings = get_harness_settings()


async def _update_paper_status_db(paper_id: str, status: str, domain_tags: list[str] | None = None) -> None:
    """同步更新 MySQL 中论文状态。"""
    try:
        from sqlalchemy import select

        from .database import async_session_factory
        from .models import Paper

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


async def _fail_pipeline(task_id: str, paper_id: str, message: str) -> None:
    from .dependencies import broker

    await broker.update(task_id, "failed", 0, message)
    await _update_paper_status_db(paper_id, "failed")


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
        task_id,
        paper_id,
        title,
        template_name,
        user_id,
    )

    if user_id is not None:
        try:
            user_settings = await load_user_settings(user_id, fallback=harness_settings)
            logger.info("User settings loaded successfully for user_id=%d", user_id)
        except ValueError as exc:
            logger.error("User settings load failed: user_id=%d error=%s", user_id, exc)
            await _fail_pipeline(task_id, paper_id, str(exc))
            return
        except Exception as exc:
            logger.error(
                "Unexpected error loading user settings: user_id=%d error=%s\n%s",
                user_id,
                exc,
                traceback.format_exc(),
            )
            await _fail_pipeline(task_id, paper_id, f"加载用户配置失败: {exc}")
            return
    else:
        user_settings = harness_settings

    app_harness = get_app_harness()
    orchestrator_ready = app_harness.is_initialized and app_harness.pipeline_orchestrator is not None
    logger.info("Pipeline routing: orchestrator_ready=%s", orchestrator_ready)

    if not orchestrator_ready:
        await _fail_pipeline(
            task_id,
            paper_id,
            "Agent harness 未初始化，无法运行 orchestrator 流水线。请检查服务启动配置。",
        )
        return

    try:
        tags = await app_harness.pipeline_orchestrator.run(
            task_id=task_id,
            paper_id=paper_id,
            title=title,
            target_language=target_language,
            template_name=template_name,
            settings=user_settings,
            user_id=user_id,
            hitl_manager=app_harness.hitl_manager,
        )
        app_harness.storage.update_paper_status(paper_id, "completed", domain_tags=tags)
        await _update_paper_status_db(paper_id, "completed", domain_tags=tags)
        logger.info("Pipeline completed via orchestrator: task_id=%s tags=%s", task_id, tags)
    except Exception as exc:
        logger.error(
            "Orchestrator pipeline failed: task_id=%s error=%s\n%s",
            task_id,
            exc,
            traceback.format_exc(),
        )
        from .dependencies import broker, storage

        storage.update_paper_status(paper_id, "failed")
        await _update_paper_status_db(paper_id, "failed")
        await broker.update(task_id, "failed", 100, f"任务失败: {exc}")
