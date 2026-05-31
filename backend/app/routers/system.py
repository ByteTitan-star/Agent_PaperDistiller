from fastapi import APIRouter

from ..config import get_settings
from ..dependencies import get_skill_registry
from ..schemas import SystemInfoResponse

router = APIRouter(tags=["system"])
settings = get_settings()

@router.get("/system/info", response_model=SystemInfoResponse)
async def get_system_info() -> SystemInfoResponse:
    use_deepseek = bool(settings.deepseek_api_key.strip())
    collaboration_mode = (
        f"Multi-Agent Collaboration: "
        f"{settings.generation_model_name} (Gen) + {settings.evaluation_model_name} (Eval)"
    )
    skill_registry = get_skill_registry()
    skill_status = skill_registry.status()
    return SystemInfoResponse(
        app_name=settings.app_name,
        model_provider="DeepSeek + DashScope(Qwen3)" if use_deepseek else settings.model_provider,
        llm_model_name=settings.deepseek_model if use_deepseek else settings.llm_model_name,
        generation_model_name=settings.generation_model_name,
        evaluation_model_name=settings.evaluation_model_name,
        collaboration_mode=collaboration_mode,
        embedding_model_name=settings.embedding_model_name,
        pipeline_mode=f"{settings.pipeline_mode} (skills={skill_status['skill_count']})",
        app_version=getattr(settings, "app_version", "V2.0"),
        app_update_date=getattr(settings, "app_update_date", "2026-05-24"),
        app_author=getattr(settings, "app_author", "ByteTitan-Star"),
        app_changelog=getattr(settings, "app_changelog", "引入 Harness 框架、ReAct Deep Search、用户系统、MySQL 持久化"),
    )