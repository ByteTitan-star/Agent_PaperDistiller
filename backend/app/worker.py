import logging

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
    except Exception as exc:
        logger.warning("Failed to update paper status in DB: %s", exc)


async def execute_pipeline(task_id: str, paper_id: str, title: str, target_language: str, template_name: str) -> None:
    app_harness = get_app_harness()
    if app_harness.is_initialized and app_harness.pipeline_harness:
        try:
            tags = await app_harness.pipeline_harness.run(
                task_id=task_id,
                paper_id=paper_id,
                title=title,
                target_language=target_language,
                template_name=template_name,
            )
            app_harness.storage.update_paper_status(paper_id, "completed", domain_tags=tags)
            await _update_paper_status_db(paper_id, "completed", domain_tags=tags)
            return
        except Exception:
            pass

    # Legacy fallback
    from .pipeline.workflow_graph import run_pipeline
    from .dependencies import broker, storage

    settings = harness_settings
    try:
        tags = await run_pipeline(
            task_id=task_id,
            paper_id=paper_id,
            title=title,
            target_language=target_language,
            template_name=template_name,
            storage=storage,
            broker=broker,
            settings=settings,
        )
        storage.update_paper_status(paper_id, "completed", domain_tags=tags)
        await _update_paper_status_db(paper_id, "completed", domain_tags=tags)
    except Exception as exc:
        storage.update_paper_status(paper_id, "failed")
        await _update_paper_status_db(paper_id, "failed")
        await broker.update(task_id, "failed", 100, f"任务失败: {exc}")
