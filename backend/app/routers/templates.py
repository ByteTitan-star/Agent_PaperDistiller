import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_admin, get_current_user
from ..database import get_db
from ..models import Template, User
from ..schemas import TemplateCreate, TemplateDetail, TemplateInfo, TemplateUpdate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["templates"])


async def _get_visible_template(
    template_id: int, user: User, db: AsyncSession
) -> Template:
    result = await db.execute(
        select(Template)
        .options(selectinload(Template.owner))
        .where(Template.id == template_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "模板不存在")
    if t.user_id is not None and t.user_id != user.id and user.role != "admin":
        raise HTTPException(403, "无权访问此模板")
    return t


async def _get_owned_template(
    template_id: int, user: User, db: AsyncSession
) -> Template:
    result = await db.execute(
        select(Template)
        .options(selectinload(Template.owner))
        .where(Template.id == template_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "模板不存在")
    if t.is_system and user.role != "admin":
        raise HTTPException(403, "系统模板不可修改")
    if t.user_id is not None and t.user_id != user.id:
        raise HTTPException(403, "只能操作自己的模板")
    return t


def _template_to_info(t: Template) -> TemplateInfo:
    return TemplateInfo(
        id=t.id,
        name=t.name,
        domain_tag=t.domain_tag or "General",
        is_default=t.is_default,
        is_system=t.is_system,
        owner_name=t.owner.username if t.owner else None,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _template_to_detail(t: Template) -> TemplateDetail:
    return TemplateDetail(
        id=t.id,
        name=t.name,
        content=t.content,
        domain_tag=t.domain_tag or "General",
        is_default=t.is_default,
        is_system=t.is_system,
        owner_name=t.owner.username if t.owner else None,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


async def _reload_template(template_id: int, db: AsyncSession) -> Template:
    """Fresh SELECT to eagerly load all columns (avoids MissingGreenlet)."""
    result = await db.execute(
        select(Template)
        .options(selectinload(Template.owner))
        .where(Template.id == template_id)
    )
    return result.scalar_one()


@router.get("/templates", response_model=list[TemplateInfo])
async def list_templates(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取模板列表（用户的 + 系统内置的）。

    前端页面：① HomeView（首页上传时的模板下拉选择）② SettingsView（设置页模板管理列表）
    用户操作：进入首页或设置页时自动加载
    """
    stmt = select(Template).options(selectinload(Template.owner))
    # 管理员可见所有模版；普通用户只看自己的 + 系统模版
    if user.role != "admin":
        stmt = stmt.where((Template.user_id == user.id) | (Template.user_id.is_(None)))
    stmt = stmt.order_by(Template.is_system.desc(), Template.created_at.desc())
    result = await db.execute(stmt)
    return [_template_to_info(t) for t in result.scalars().all()]


@router.get("/templates/{template_id}", response_model=TemplateDetail)
async def get_template(
    template_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取模板详情（含完整内容）。

    前端页面：SettingsView（设置页模板管理）
    用户操作：点击模板列表中某条模板的「查看」或「编辑」按钮
    """
    t = await _get_visible_template(template_id, user, db)
    return _template_to_detail(t)


@router.post("/templates", response_model=TemplateDetail, status_code=201)
async def create_template(
    body: TemplateCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """新建自定义摘要模板。

    前端页面：SettingsView（设置页模板管理）
    用户操作：点击「新建模板」→ 填写名称/内容/领域标签 → 点击「保存」
    """
    existing = await db.execute(
        select(Template).where(Template.user_id == user.id, Template.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "同名模板已存在")
    t = Template(
        name=body.name,
        content=body.content,
        domain_tag=body.domain_tag,
        user_id=user.id,
    )
    db.add(t)
    try:
        await db.flush()
    except DataError as e:
        logger.error("Template save failed (DataError): %s", e)
        raise HTTPException(
            422,
            "模板内容包含数据库不支持的字符，请检查 MySQL 表是否使用 utf8mb4 字符集。",
        )
    logger.info("Template created: id=%d name=%s user=%s", t.id, t.name, user.username)
    t = await _reload_template(t.id, db)
    return _template_to_detail(t)


@router.post("/templates/upload", response_model=TemplateDetail, status_code=201)
async def upload_template(
    file: UploadFile = File(...),
    domain_tag: str = Form(default="General"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传 .md 文件作为摘要模板。

    前端页面：SettingsView（设置页模板管理）
    用户操作：点击「上传 .md」按钮 → 选择本地 Markdown 文件上传
    """
    if not file.filename or not file.filename.lower().endswith(".md"):
        raise HTTPException(400, "仅支持上传 .md 文件")
    content = (await file.read()).decode("utf-8", errors="replace")
    name = file.filename

    existing = await db.execute(
        select(Template).where(Template.user_id == user.id, Template.name == name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "同名模板已存在")

    t = Template(
        name=name,
        content=content,
        domain_tag=domain_tag,
        user_id=user.id,
    )
    db.add(t)
    await db.flush()
    t = await _reload_template(t.id, db)
    return _template_to_detail(t)


@router.put("/templates/{template_id}", response_model=TemplateDetail)
async def update_template(
    template_id: int,
    body: TemplateUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑已有模板。

    前端页面：SettingsView（设置页模板管理）
    用户操作：点击模板的「编辑」→ 修改内容 → 点击「保存」
    """
    t = await _get_owned_template(template_id, user, db)
    if body.name is not None:
        t.name = body.name
    if body.content is not None:
        t.content = body.content
    if body.domain_tag is not None:
        t.domain_tag = body.domain_tag
    await db.flush()
    t = await _reload_template(t.id, db)
    return _template_to_detail(t)


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除用户自定义模板。

    前端页面：SettingsView（设置页模板管理）
    用户操作：点击模板列表中某条模板的「删除」按钮
    """
    t = await _get_owned_template(template_id, user, db)
    await db.delete(t)
    await db.flush()


# ---------------------------------------------------------------------------
# Admin: Template Management
# ---------------------------------------------------------------------------
@router.get("/admin/templates")
async def admin_list_templates(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员查看所有模板（含上传者信息）。

    前端页面：AdminView（管理员页）「模板管理」Tab
    用户操作：管理员点击「模板管理」Tab 自动加载
    """
    result = await db.execute(
        select(Template)
        .options(selectinload(Template.owner))
        .order_by(Template.created_at.desc())
    )
    return [
        {
            "id": t.id,
            "name": t.name,
            "domain_tag": t.domain_tag or "General",
            "is_default": t.is_default,
            "is_system": t.is_system,
            "user_id": t.user_id,
            "owner_name": t.owner.username if t.owner else None,
            "owner_email": t.owner.email if t.owner else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in result.scalars().all()
    ]


@router.delete("/admin/templates/{template_id}", status_code=204)
async def admin_delete_template(
    template_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员删除任意模板，并邮件通知模板所有者。

    前端页面：AdminView（管理员页）「模板管理」Tab
    用户操作：管理员在模板表格中点击「删除」按钮
    """
    result = await db.execute(
        select(Template)
        .options(selectinload(Template.owner))
        .where(Template.id == template_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "模板不存在")
    if t.is_system:
        raise HTTPException(403, "系统内置模板不可删除")

    owner_email = t.owner.email if t.owner else None
    owner_name = t.owner.username if t.owner else None
    template_name = t.name

    await db.delete(t)
    await db.flush()

    # 异步通知模板所有者
    if owner_email:
        from ..auth.email_service import send_template_deleted_email
        await send_template_deleted_email(owner_email, owner_name, template_name)
