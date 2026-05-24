"""
   FastAPI 应用入口：注册中间件、路由、lifespan。
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent_skills import SkillRegistry
from .config import get_settings
from .pipeline.state_broker import TaskBroker
from .storage import Storage

# ---------------------------------------------------------------------------
# 应用级依赖单例
# ---------------------------------------------------------------------------
settings = get_settings()
backend_root = Path(__file__).resolve().parents[1]
project_root = backend_root.parent

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
    skills_root=project_root / settings.agent_skills_dir,
    vector_db_dir=backend_root / settings.data_dir / settings.vector_db_subdir,
    embedding_model_name=settings.embedding_model_name,
    provider=settings.vector_store_provider,
    collection_name=settings.skills_collection_name,
)
skill_registry.load()


# ---------------------------------------------------------------------------
# Lifespan: 数据库表自动创建
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    from .database import engine
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
app.include_router(tasks_router, prefix=api)
app.include_router(settings_router, prefix=api)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
