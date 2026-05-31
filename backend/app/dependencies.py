from pathlib import Path

from .config import get_settings
from .storage import OSSClient, Storage
from .pipeline.state_broker import TaskBroker
from .agent_skills import SkillRegistry

settings = get_settings()
backend_root = Path(__file__).resolve().parent.parent  # backend/app/dependencies.py -> backend/
app_root = Path(__file__).resolve().parent             # backend/app/

# 初始化 OSS 客户端（如果配置了）
oss_client = OSSClient(
    access_key_id=settings.oss_access_key_id,
    access_key_secret=settings.oss_access_key_secret,
    endpoint=settings.oss_endpoint,
    bucket_name=settings.oss_bucket_name,
    prefix=settings.oss_prefix,
) if settings.oss_enabled else None

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
    """Lazily return the AppHarness singleton."""
    from .harness.app import get_app_harness
    return get_app_harness()
