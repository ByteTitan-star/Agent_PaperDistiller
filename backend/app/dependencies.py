from pathlib import Path

from .agent_skills import SkillRegistry
from .config import get_settings
from .pipeline.state_broker import TaskBroker
from .storage import OSSClient, Storage

settings = get_settings()
backend_root = Path(__file__).resolve().parent.parent  # backend/app/dependencies.py -> backend/
app_root = Path(__file__).resolve().parent  # backend/app/

# 初始化 OSS 客户端（如果配置了）
oss_client = (
    OSSClient(
        access_key_id=settings.oss_access_key_id,
        access_key_secret=settings.oss_access_key_secret,
        endpoint=settings.oss_endpoint,
        bucket_name=settings.oss_bucket_name,
        prefix=settings.oss_prefix,
    )
    if settings.oss_enabled
    else None
)

storage = Storage(
    base_dir=backend_root / settings.data_dir,
    templates_dir=backend_root / settings.templates_dir,
    vector_provider=settings.vector_store_provider,
    vector_collection_name=settings.vector_collection_name,
    vector_db_subdir=settings.vector_db_subdir,
    embedding_model_name=settings.embedding_model_name,
    vector_distance_metric=settings.vector_distance_metric,
    oss_client=oss_client,
)
broker = TaskBroker()

skill_registry = SkillRegistry(
    skills_root=app_root / settings.agent_skills_dir,
    vector_db_dir=backend_root / settings.data_dir / settings.vector_db_subdir,
    embedding_model_name=settings.embedding_model_name,
    provider=settings.vector_store_provider,
    collection_name=settings.skills_collection_name,
)
# 懒加载：不在模块导入时调用 load()，避免阻塞 torch 加载
# load() 会在首次使用 select_tools() 时自动触发
_skill_registry_loaded = False


def get_skill_registry() -> SkillRegistry:
    """返回已加载的 SkillRegistry 单例（首次调用时触发 load）。"""
    global _skill_registry_loaded
    if not _skill_registry_loaded:
        skill_registry.load()
        _skill_registry_loaded = True
    return skill_registry


def get_app_harness():
    """返回注入了 storage/broker/skill_registry 单例的 AppHarness。

    所有调用方（main lifespan、worker）都应走这里，确保 AppHarness 复用
    dependencies.py 构造的同一套单例（含 OSS 挂载的 Storage），而不是另建一套。
    """
    from .harness.app import get_app_harness as _get_app_harness

    return _get_app_harness(storage=storage, broker=broker, skill_registry=skill_registry)


def get_tool_executor():
    """返回统一的工具执行面：优先 agent ToolRegistry，其次 HarnessToolRegistry，最后 SkillRegistry。"""
    try:
        from .agent.bootstrap import get_runtime

        runtime = get_runtime()
        if runtime.registry is not None:
            return _AgentToolExecutorAdapter(runtime)
    except Exception:
        pass
    try:
        harness = get_app_harness()
        if harness.is_initialized and harness.tool_harness is not None:
            return harness.tool_harness
    except Exception:
        pass
    return get_skill_registry()


class _AgentToolExecutorAdapter:
    """Bridge legacy skill_executor.execute(name, args) callers to ToolRegistry."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime

    def execute(self, name: str, args: dict, context: dict | None = None) -> dict:
        import asyncio

        from .agent.schemas import AuthContext
        from .tools.base import ToolContext

        auth = AuthContext(user_id=0, paper_id=(context or {}).get("paper_id"))
        tool_ctx = ToolContext(session_id="legacy", auth=auth, paper_id=auth.paper_id, runtime=self._runtime)

        async def _run():
            result = await self._runtime.registry.execute(name, args, context=tool_ctx)
            return {"content": result.content, "status": result.status.value}

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())
        future = asyncio.ensure_future(_run())
        if future.done():
            return future.result()
        # Called from sync context inside async app — fall back to skill registry
        return self._runtime.skill_registry.execute(name, args, context=context)

    def select_tools(self, *args, **kwargs):
        return self._runtime.skill_registry.select_tools(*args, **kwargs)

    def build_openai_tools(self, *args, **kwargs):
        return self._runtime.skill_registry.build_openai_tools(*args, **kwargs)
