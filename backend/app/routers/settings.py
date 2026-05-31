from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, case
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

    # 热更新运行时配置
    from ..main import storage
    for item in body.settings:
        if item.setting_key == "default_template" and item.setting_value:
            storage.default_template = item.setting_value

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


# ---------------------------------------------------------------------------
# Admin: Token Usage Statistics
# ---------------------------------------------------------------------------
@router.get("/admin/token-stats/overview")
async def token_stats_overview(
    period: str = Query(default="daily", regex="^(daily|weekly|monthly)$"),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """系统级 Token 用量概览：总量、按模型分布、按日期趋势。"""
    from ..models import TokenUsageLog

    filters = []
    if start_date:
        filters.append(TokenUsageLog.created_at >= start_date)
    if end_date:
        filters.append(TokenUsageLog.created_at <= end_date + " 23:59:59")

    # 汇总
    q = select(
        func.sum(TokenUsageLog.prompt_tokens).label("total_prompt"),
        func.sum(TokenUsageLog.completion_tokens).label("total_completion"),
        func.sum(TokenUsageLog.total_tokens).label("total_tokens"),
        func.count(TokenUsageLog.id).label("total_calls"),
    )
    for f in filters:
        q = q.where(f)
    result = await db.execute(q)
    row = result.one()

    # 按模型分布
    q_model = select(
        TokenUsageLog.model_name,
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
    )
    for f in filters:
        q_model = q_model.where(f)
    q_model = q_model.group_by(TokenUsageLog.model_name)
    model_result = await db.execute(q_model)

    # 按日期趋势
    if period == "daily":
        date_expr = func.date(TokenUsageLog.created_at)
    elif period == "weekly":
        date_expr = func.yearweek(TokenUsageLog.created_at)
    else:
        date_expr = func.date_format(TokenUsageLog.created_at, "%Y-%m")
    q_trend = select(
        date_expr.label("period"),
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
        func.sum(TokenUsageLog.prompt_tokens).label("prompt"),
        func.sum(TokenUsageLog.completion_tokens).label("completion"),
    )
    for f in filters:
        q_trend = q_trend.where(f)
    q_trend = q_trend.group_by("period").order_by("period")
    trend_result = await db.execute(q_trend)

    # 按操作类型
    q_action = select(
        TokenUsageLog.action_type,
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
    )
    for f in filters:
        q_action = q_action.where(f)
    q_action = q_action.group_by(TokenUsageLog.action_type)
    action_result = await db.execute(q_action)

    return {
        "totals": {
            "prompt": int(row.total_prompt or 0),
            "completion": int(row.total_completion or 0),
            "total": int(row.total_tokens or 0),
            "calls": int(row.total_calls or 0),
        },
        "by_model": [
            {"model": r.model_name, "tokens": int(r.tokens or 0)}
            for r in model_result
        ],
        "by_date": [
            {"period": str(r.period), "tokens": int(r.tokens or 0),
             "prompt": int(r.prompt or 0), "completion": int(r.completion or 0)}
            for r in trend_result
        ],
        "by_action": [
            {"action": r.action_type, "tokens": int(r.tokens or 0)}
            for r in action_result
        ],
    }


@router.get("/admin/token-stats/users")
async def token_stats_users(
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """所有用户的 Token 用量排行。"""
    from ..models import TokenUsageLog, User as UserModel

    filters = []
    if start_date:
        filters.append(TokenUsageLog.created_at >= start_date)
    if end_date:
        filters.append(TokenUsageLog.created_at <= end_date + " 23:59:59")

    q = (
        select(
            TokenUsageLog.user_id,
            UserModel.username,
            UserModel.email,
            func.sum(TokenUsageLog.total_tokens).label("total_tokens"),
            func.count(TokenUsageLog.id).label("calls"),
        )
        .outerjoin(UserModel, TokenUsageLog.user_id == UserModel.id)
        .group_by(TokenUsageLog.user_id, UserModel.username, UserModel.email)
        .order_by(func.sum(TokenUsageLog.total_tokens).desc())
    )
    for f in filters:
        q = q.where(f)
    result = await db.execute(q)

    return [
        {
            "user_id": r.user_id,
            "username": r.username or "未知",
            "email": r.email or "",
            "total_tokens": int(r.total_tokens or 0),
            "calls": int(r.calls or 0),
        }
        for r in result
    ]


@router.get("/admin/token-stats/users/{user_id}")
async def token_stats_user_detail(
    user_id: int,
    period: str = Query(default="daily", regex="^(daily|weekly|monthly)$"),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """单个用户的 Token 用量明细。"""
    from ..models import TokenUsageLog

    filters = [TokenUsageLog.user_id == user_id]
    if start_date:
        filters.append(TokenUsageLog.created_at >= start_date)
    if end_date:
        filters.append(TokenUsageLog.created_at <= end_date + " 23:59:59")

    if period == "daily":
        date_expr = func.date(TokenUsageLog.created_at)
    elif period == "weekly":
        date_expr = func.yearweek(TokenUsageLog.created_at)
    else:
        date_expr = func.date_format(TokenUsageLog.created_at, "%Y-%m")

    q = select(
        date_expr.label("period"),
        func.sum(TokenUsageLog.total_tokens).label("tokens"),
        func.sum(TokenUsageLog.prompt_tokens).label("prompt"),
        func.sum(TokenUsageLog.completion_tokens).label("completion"),
        func.count(TokenUsageLog.id).label("calls"),
    )
    for f in filters:
        q = q.where(f)
    q = q.group_by("period").order_by("period")
    result = await db.execute(q)

    return [
        {
            "period": str(r.period),
            "tokens": int(r.tokens or 0),
            "prompt": int(r.prompt or 0),
            "completion": int(r.completion or 0),
            "calls": int(r.calls or 0),
        }
        for r in result
    ]
