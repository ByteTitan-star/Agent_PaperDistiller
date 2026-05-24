from pathlib import Path

from .config import get_settings
from .storage import Storage
from .pipeline.state_broker import TaskBroker
from .agent_skills import SkillRegistry

settings = get_settings()
backend_root = Path(__file__).resolve().parent.parent  # backend/app/dependencies.py -> backend/
project_root = backend_root.parent                     # backend/ -> 5git-AgentPaperDistiller/

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


def get_app_harness():
    """Lazily return the AppHarness singleton."""
    from .harness.app import get_app_harness
    return get_app_harness()
