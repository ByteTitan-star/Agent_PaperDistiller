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
    ok = await check_email_code(db, body.email, body.code, action="register")
    if not ok:
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    return {"message": "验证码正确"}


# ---------------------------------------------------------------------------
# Step 3: 完成注册（消费验证码 + 创建用户）
# ---------------------------------------------------------------------------
@router.post("/register")
async def register(body: RegisterFinal, db: AsyncSession = Depends(get_db)):
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
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")

    user.last_login_at = dt.datetime.now(dt.timezone.utc)
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
    return UserResponse.model_validate(user)


# ---------------------------------------------------------------------------
# 重发验证码
# ---------------------------------------------------------------------------
@router.post("/resend-verify")
async def resend_verify(body: ResendVerifyRequest, db: AsyncSession = Depends(get_db)):
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
    result = await db.execute(select(User).order_by(User.id))
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


@router.put("/users/{user_id}/role")
async def change_role(
    user_id: int,
    role: str = "user",
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
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
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_active = is_active
    await db.flush()
    return {"message": f"用户 {user.username} 状态已更新"}
