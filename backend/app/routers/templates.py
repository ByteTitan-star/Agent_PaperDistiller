from fastapi import APIRouter

from ..dependencies import storage
from ..schemas import TemplateInfo

router = APIRouter(tags=["templates"])

@router.get("/templates", response_model=list[TemplateInfo])
async def list_templates() -> list[TemplateInfo]:
    return [TemplateInfo(name=name) for name in storage.list_templates()]