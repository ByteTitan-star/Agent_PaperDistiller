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

from .config import get_settings

# ---------------------------------------------------------------------------
# 应用级依赖单例 —— 统一由 dependencies.py 构造（唯一来源，且 Storage 已挂载 OSS）。
# 此处仅重导出，以兼容历史 `from ..main import storage`（routers/settings.py）。
# 旧代码在 main.py 里又构造了一份 storage/broker/skill_registry，导致运行时存在两套
# 实例（且 main 的那份 Storage 没挂 OSS），现已消除。
# ---------------------------------------------------------------------------
settings = get_settings()
backend_root = Path(__file__).resolve().parents[1]
app_root = Path(__file__).resolve().parent

from .dependencies import storage, broker, skill_registry  # noqa: E402,F401

logger = logging.getLogger(__name__)


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

    # 初始化 AppHarness —— 让 harness 成为执行脊柱（agents/collaboration/tools/pipeline）。
    # 失败仅记日志、不阻断启动；worker 会回退到 legacy 线性流水线。
    if settings.harness_startup_enabled:
        try:
            from .dependencies import get_app_harness
            _harness = get_app_harness()
            await _harness.startup()
            logger.info("AppHarness started: initialized=%s", _harness.is_initialized)
        except Exception:
            logger.exception("AppHarness startup failed; pipeline will fall back to legacy linear")

    # 对外 MCP server（把技能暴露为标准 MCP 工具）。默认关闭，需 pip install mcp。
    if settings.mcp_enabled:
        try:
            from .dependencies import get_tool_executor, get_skill_registry
            from .harness.mcp.server import build_mcp_http_app
            get_skill_registry()  # 确保技能已加载，MCP 才能列出工具
            mcp_app = build_mcp_http_app(get_tool_executor())
            if mcp_app is not None:
                app.mount(settings.mcp_mount_path, mcp_app)
                logger.info("MCP server mounted at %s", settings.mcp_mount_path)
            else:
                logger.warning("MCP server not mounted (no tools or mcp unavailable)")
        except Exception:
            logger.exception("MCP server mount failed; skipping")

    # OpenTelemetry 自托管可观测（默认关闭）。启用后 FastAPI 请求 + harness Tracer spans 都会上报。
    if settings.otel_enabled:
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

            provider = TracerProvider(
                resource=Resource.create({"service.name": settings.otel_service_name})
            )
            if settings.otel_exporter_otlp_endpoint:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
                )
            else:
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)

            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
                FastAPIInstrumentor.instrument_app(app)
            except Exception:
                logger.warning("FastAPIInstrumentor not available; only harness spans will be exported")

            logger.info("OpenTelemetry enabled (exporter=%s)", settings.otel_exporter_otlp_endpoint or "console")
        except Exception:
            logger.exception("OTel init failed; tracing disabled")

    yield

    if settings.harness_startup_enabled:
        try:
            from .dependencies import get_app_harness
            await get_app_harness().shutdown()
        except Exception:
            logger.exception("AppHarness shutdown failed")
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
from .routers.hitl import router as hitl_router
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
app.include_router(hitl_router, prefix=api)
app.include_router(tasks_router, prefix=api)
app.include_router(settings_router, prefix=api)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
