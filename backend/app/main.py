"""
   FastAPI 应用入口：注册中间件、路由、lifespan。
"""
import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# 日志配置：控制台 + 文件
# ---------------------------------------------------------------------------
_backend_root = Path(__file__).resolve().parents[1]
_log_dir = _backend_root / "logs"
_log_dir.mkdir(exist_ok=True)

_log_fmt = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 文件日志：按大小轮转，保留 5 个备份
_file_handler = RotatingFileHandler(
    _log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(_log_fmt)

# 控制台日志
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_fmt)

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.addHandler(_file_handler)
_root_logger.addHandler(_console_handler)

from .agent_skills import SkillRegistry
from .config import get_settings
from .pipeline.state_broker import TaskBroker
from .storage import Storage

# ---------------------------------------------------------------------------
# 应用级依赖单例
# ---------------------------------------------------------------------------
settings = get_settings()
backend_root = Path(__file__).resolve().parents[1]
app_root = Path(__file__).resolve().parent

storage = Storage(
    base_dir=backend_root / settings.data_dir,
    templates_dir=backend_root / settings.templates_dir,
    vector_provider=settings.vector_store_provider,
    vector_collection_name=settings.vector_collection_name,
    vector_db_subdir=settings.vector_db_subdir,
    embedding_model_name=settings.embedding_model_name,
    vector_distance_metric=settings.vector_distance_metric,
)
broker = TaskBroker()

skill_registry = SkillRegistry(
    skills_root=app_root / settings.agent_skills_dir,
    vector_db_dir=backend_root / settings.data_dir / settings.vector_db_subdir,
    embedding_model_name=settings.embedding_model_name,
    provider=settings.vector_store_provider,
    collection_name=settings.skills_collection_name,
)
# 懒加载：首次 select_tools() 时自动 load()，避免启动时阻塞 torch
_skill_registry_loaded = False


# ---------------------------------------------------------------------------
# Lifespan: 数据库表自动创建
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .database import engine
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 从 system_settings 表加载运行时配置 + 种子系统模板
    try:
        from .database import async_session_factory
        from .models import SystemSetting, Template
        from .storage import domain_tag_from_template
        from sqlalchemy import select

        async with async_session_factory() as session:
            result = await session.execute(
                select(SystemSetting).where(SystemSetting.setting_key == "default_template")
            )
            setting = result.scalar_one_or_none()
            if setting and setting.setting_value:
                storage.default_template = setting.setting_value

            templates_dir = backend_root / settings.templates_dir
            if templates_dir.exists():
                for md_file in templates_dir.glob("*.md"):
                    name = md_file.name
                    existing = await session.execute(
                        select(Template).where(
                            Template.name == name, Template.is_system == True
                        )
                    )
                    if not existing.scalar_one_or_none():
                        content = md_file.read_text(encoding="utf-8")
                        session.add(Template(
                            name=name,
                            content=content,
                            domain_tag=domain_tag_from_template(name),
                            is_system=True,
                            user_id=None,
                        ))
                await session.commit()
    except Exception:
        pass

    yield
    await engine.dispose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS + Audit Middleware
# ---------------------------------------------------------------------------
from .middleware.audit import AuditLogMiddleware

app.add_middleware(AuditLogMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 注册路由
# ---------------------------------------------------------------------------
from .auth.router import router as auth_router
from .routers.chat_history import router as chat_history_router
from .routers.health import router as health_router
from .routers.papers import router as papers_router
from .routers.settings import router as settings_router
from .routers.system import router as system_router
from .routers.tasks import router as tasks_router
from .routers.templates import router as templates_router
from .routers.upload import router as upload_router

api = settings.api_prefix

app.include_router(health_router, prefix=api)
app.include_router(system_router, prefix=api)
app.include_router(auth_router, prefix=api)
app.include_router(templates_router, prefix=api)
app.include_router(upload_router, prefix=api)
app.include_router(papers_router, prefix=api)
app.include_router(chat_history_router, prefix=api)
app.include_router(tasks_router, prefix=api)
app.include_router(settings_router, prefix=api)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
