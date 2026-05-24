from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.crypto import aes_decrypt, aes_encrypt
from ..auth.dependencies import get_current_admin, get_current_user
from ..database import get_db
from ..models import SystemSetting, User, UserApiConfig

router = APIRouter(tags=["settings"])


def _mask_key(key: str | None) -> str | None:
    if not key:
        return None
    return key[:4] + "****" + key[-4:] if len(key) > 8 else "****"


class ApiKeysResponse(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    qwen_api_key: str | None = None
    qwen_base_url: str | None = None
    tavily_api_key: str | None = None


class ApiKeysUpdate(BaseModel):
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    qwen_api_key: str | None = None
    qwen_base_url: str | None = None
    tavily_api_key: str | None = None


class SystemSettingItem(BaseModel):
    setting_key: str
    setting_value: str | None = None
    setting_type: str = "string"
    description: str | None = None

    model_config = {"from_attributes": True}


class SystemSettingUpdate(BaseModel):
    settings: list[SystemSettingItem]


@router.get("/settings/api-keys", response_model=ApiKeysResponse)
async def get_api_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserApiConfig).where(UserApiConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        return ApiKeysResponse()

    def safe_decrypt(val: str | None) -> str | None:
        if not val:
            return None
        try:
            return _mask_key(aes_decrypt(val))
        except Exception:
            return _mask_key(val)

    return ApiKeysResponse(
        deepseek_api_key=safe_decrypt(config.deepseek_api_key),
        deepseek_base_url=config.deepseek_base_url,
        qwen_api_key=safe_decrypt(config.qwen_api_key),
        qwen_base_url=config.qwen_base_url,
        tavily_api_key=safe_decrypt(config.tavily_api_key),
    )


@router.put("/settings/api-keys")
async def update_api_keys(
    body: ApiKeysUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserApiConfig).where(UserApiConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()
    if not config:
        config = UserApiConfig(user_id=user.id)
        db.add(config)
        await db.flush()
        result = await db.execute(
            select(UserApiConfig).where(UserApiConfig.user_id == user.id)
        )
        config = result.scalar_one()

    def encrypt_if_set(val: str | None, current: str | None) -> str | None:
        if val is None:
            return current
        if val.strip() == "":
            return None
        return aes_encrypt(val)

    config.deepseek_api_key = encrypt_if_set(body.deepseek_api_key, config.deepseek_api_key)
    config.deepseek_base_url = body.deepseek_base_url if body.deepseek_base_url is not None else config.deepseek_base_url
    config.qwen_api_key = encrypt_if_set(body.qwen_api_key, config.qwen_api_key)
    config.qwen_base_url = body.qwen_base_url if body.qwen_base_url is not None else config.qwen_base_url
    config.tavily_api_key = encrypt_if_set(body.tavily_api_key, config.tavily_api_key)
    await db.flush()
    return {"message": "API 配置已更新"}


# ---------------------------------------------------------------------------
# Admin: System Settings
# ---------------------------------------------------------------------------
@router.get("/admin/settings", response_model=list[SystemSettingItem])
async def list_system_settings(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(SystemSetting).order_by(SystemSetting.id))
    return [SystemSettingItem.model_validate(s) for s in result.scalars().all()]


@router.put("/admin/settings")
async def update_system_settings(
    body: SystemSettingUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    for item in body.settings:
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.setting_key == item.setting_key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            setting.setting_value = item.setting_value
            if item.description:
                setting.description = item.description
        else:
            db.add(SystemSetting(
                setting_key=item.setting_key,
                setting_value=item.setting_value,
                setting_type=item.setting_type,
                description=item.description,
            ))
    await db.flush()
    return {"message": "系统配置已更新"}


# ---------------------------------------------------------------------------
# Admin: Paper Management
# ---------------------------------------------------------------------------
@router.get("/admin/papers")
async def admin_list_papers(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from ..schemas import PaperMeta
    from ..storage import domain_tag_from_template, unique_keep_order

    result = await db.execute(select(UserApiConfig.__class__.__mro__[0]).order_by(
        __import__("sqlalchemy").desc("created_at") if False else User.id
    ))
    # Simple query for all papers
    from ..models import Paper
    result = await db.execute(select(Paper).order_by(Paper.created_at.desc()))
    papers = result.scalars().all()
    return [
        {
            "id": p.id,
            "paper_id": p.paper_id,
            "title": p.title,
            "status": p.status,
            "user_id": p.user_id,
            "is_public": p.is_public,
            "created_at": p.created_at.isoformat(),
        }
        for p in papers
    ]


@router.delete("/admin/papers/{paper_id}")
async def admin_delete_paper(
    paper_id: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    from ..models import Paper

    result = await db.execute(select(Paper).where(Paper.paper_id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在")
    await db.delete(paper)
    return {"message": "论文已删除"}
