import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models import User
from ..schemas import (
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterFinal,
    ResendVerifyRequest,
    SendCodeRequest,
    TokenResponse,
    UserLogin,
    UserResponse,
    VerifyCodeRequest,
)
from .crypto import hash_password, verify_password
from .dependencies import get_current_admin, get_current_user
from .email_service import (
    check_email_code,
    create_email_code,
    send_code_email,
    verify_email_code,
)
from .jwt_utils import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


# ---------------------------------------------------------------------------
# Step 1: 发送验证码
# ---------------------------------------------------------------------------
@router.post("/send-code")
async def send_code(body: SendCodeRequest, db: AsyncSession = Depends(get_db)):
    """发送邮箱验证码（注册用）。

    前端页面：LoginView（登录注册页）注册流程
    用户操作：填写邮箱 → 点击「发送验证码」按钮
    """
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    code = await create_email_code(db, body.email, action="register", expire_minutes=10)
    await send_code_email(body.email, code)
    return {"message": "验证码已发送"}


# ---------------------------------------------------------------------------
# Step 2: 校验验证码（不消费，仅前端预验证）
# ---------------------------------------------------------------------------
@router.post("/verify-code")
async def verify_code(body: VerifyCodeRequest, db: AsyncSession = Depends(get_db)):
    """预验证验证码（不消耗，仅前端校验用）。

    前端页面：LoginView（登录注册页）注册流程
    用户操作：输入验证码后点击「下一步」（前端预校验，验证码不消耗）
    """
    ok = await check_email_code(db, body.email, body.code, action="register")
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    return {"message": "验证码正确"}


# ---------------------------------------------------------------------------
# Step 3: 完成注册（消费验证码 + 创建用户）
# ---------------------------------------------------------------------------
@router.post("/register")
async def register(body: RegisterFinal, db: AsyncSession = Depends(get_db)):
    """完成注册（消费验证码 + 创建用户）。

    前端页面：LoginView（登录注册页）注册流程
    用户操作：填写用户名 + 密码 → 点击「完成注册」按钮
    """
    ok = await verify_email_code(db, body.email, body.code, action="register")
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    user = User(
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
        role="user",
        is_active=True,
        email_verified=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return {"message": "注册成功，请登录"}


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    """用户登录，返回 JWT token 和用户信息。

    前端页面：LoginView（登录注册页）
    用户操作：填写邮箱 + 密码 → 点击「登录」按钮
    """
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    user.last_login_at = dt.datetime.now(dt.UTC)
    await db.flush()
    await db.refresh(user)

    access_token = create_access_token({"sub": str(user.id)})
    user_resp = UserResponse.model_validate(user)
    return TokenResponse(access_token=access_token, user=user_resp)


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """获取当前登录用户信息。

    前端页面：全局调用（App.vue 路由守卫 / authStore）
    用户操作：① 页面刷新时自动验证身份 ② 登录成功后回调获取用户信息
    """
    return UserResponse.model_validate(user)


# ---------------------------------------------------------------------------
# 重发验证码
# ---------------------------------------------------------------------------
@router.post("/resend-verify")
async def resend_verify(body: ResendVerifyRequest, db: AsyncSession = Depends(get_db)):
    """重发注册验证码。

    前端页面：LoginView（登录注册页）注册流程
    用户操作：验证码过期后点击「重发」按钮
    """
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    code = await create_email_code(db, body.email, action="register", expire_minutes=10)
    await send_code_email(body.email, code)
    return {"message": "验证码已重新发送"}


# ---------------------------------------------------------------------------
# 忘记密码
# ---------------------------------------------------------------------------
@router.post("/forgot-password")
async def forgot_password(body: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    """忘记密码 — 发送重置验证码。

    前端页面：LoginView（登录注册页）密码找回流程
    用户操作：点击「忘记密码」→ 填写注册邮箱 → 提交
    """
    result = await db.execute(select(User).where(User.email == body.email))
    if not result.scalar_one_or_none():
        return {"message": "如果该邮箱已注册，验证码已发送"}

    code = await create_email_code(db, body.email, action="reset_password", expire_minutes=10)
    await send_code_email(body.email, code)
    return {"message": "如果该邮箱已注册，验证码已发送"}


# ---------------------------------------------------------------------------
# 重置密码
# ---------------------------------------------------------------------------
@router.post("/reset-password")
async def reset_password(body: PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    """重置密码（使用验证码验证身份）。

    前端页面：LoginView（登录注册页）密码重置流程
    用户操作：输入验证码 + 新密码 → 提交重置
    """
    ok = await verify_email_code(db, body.email, body.token, action="reset_password")
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.hashed_password = hash_password(body.new_password)
    await db.flush()
    return {"message": "密码重置成功"}


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@router.get("/users", response_model=list[UserResponse])
async def list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员获取所有用户列表。

    前端页面：AdminView（管理员页）「用户管理」Tab
    用户操作：管理员点击「用户管理」Tab 自动加载用户表格
    """
    result = await db.execute(select(User).order_by(User.id))
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


@router.put("/users/{user_id}/role")
async def change_role(
    user_id: int,
    role: str = "user",
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员修改用户角色（user ↔ admin）。

    前端页面：AdminView（管理员页）「用户管理」Tab
    用户操作：管理员在用户表格中修改角色下拉框（user ↔ admin）
    """
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="无效角色")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.role = role
    await db.flush()
    return {"message": f"用户 {user.username} 角色已更新为 {role}"}


@router.put("/users/{user_id}/status")
async def change_status(
    user_id: int,
    is_active: bool = True,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员启用/禁用用户账号。

    前端页面：AdminView（管理员页）「用户管理」Tab
    用户操作：管理员在用户表格中切换启用/禁用开关
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_active = is_active
    await db.flush()
    return {"message": f"用户 {user.username} 状态已更新"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """管理员删除用户（先发邮件通知，再级联删除关联数据）。

    前端页面：AdminView（管理员页）「用户管理」Tab
    用户操作：管理员在用户表格中点击「删除用户」按钮
    """
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    target_email = user.email
    target_name = user.username

    # 先发通知邮件，再删除
    from .email_service import send_account_deleted_email

    await send_account_deleted_email(target_email, target_name)

    # 手动级联删除关联数据（MySQL 外键无 CASCADE）
    from ..models import ChatSession, Paper, Template, UserApiConfig

    for model in [Paper, Template, ChatSession, UserApiConfig]:
        rows = await db.execute(select(model).where(model.user_id == user_id))
        for row in rows.scalars().all():
            await db.delete(row)

    await db.delete(user)
    await db.flush()
    return {"message": f"用户 {target_name} 已删除"}
