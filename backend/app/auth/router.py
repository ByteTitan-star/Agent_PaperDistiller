import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models import SystemSetting, User
from ..schemas import (
    EmailVerifyRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    ResendVerifyRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)
from .crypto import hash_password, verify_password
from .dependencies import get_current_admin, get_current_user
from .email_service import create_email_verification, send_verification_email, verify_email_token
from .jwt_utils import create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


async def _require_email_verify_enabled(db: AsyncSession) -> bool:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.setting_key == "register_requires_email_verify")
    )
    row = result.scalar_one_or_none()
    return row and row.setting_value == "true"


# ---------------------------------------------------------------------------
# POST /api/auth/register
# ---------------------------------------------------------------------------
@router.post("/register", response_model=TokenResponse)
async def register(body: UserRegister, request: Request, db: AsyncSession = Depends(get_db)):
    # 检查邮箱是否已注册
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    user = User(
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
        role="user",
        email_verified=False,
    )
    db.add(user)
    await db.flush()

    # 是否需要邮箱验证
    require_verify = await _require_email_verify_enabled(db)
    if require_verify:
        token = await create_email_verification(db, body.email, action="register")
        base_url = str(request.base_url).rstrip("/")
        await send_verification_email(body.email, token, base_url)

    access_token = create_access_token({"sub": str(user.id)})
    user_resp = UserResponse.model_validate(user)
    return TokenResponse(access_token=access_token, user=user_resp)


# ---------------------------------------------------------------------------
# POST /api/auth/login
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
# POST /api/auth/verify-email
# ---------------------------------------------------------------------------
@router.post("/verify-email")
async def verify_email(body: EmailVerifyRequest, db: AsyncSession = Depends(get_db)):
    ok = await verify_email_token(db, body.email, body.token, action="register")
    if not ok:
        raise HTTPException(status_code=400, detail="验证链接无效或已过期")
    return {"message": "邮箱验证成功"}


# ---------------------------------------------------------------------------
# POST /api/auth/resend-verify
# ---------------------------------------------------------------------------
@router.post("/resend-verify")
async def resend_verify(body: ResendVerifyRequest, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="邮箱已验证")

    token = await create_email_verification(db, body.email, action="register")
    base_url = str(request.base_url).rstrip("/")
    await send_verification_email(body.email, token, base_url)
    return {"message": "验证邮件已重新发送"}


# ---------------------------------------------------------------------------
# POST /api/auth/forgot-password
# ---------------------------------------------------------------------------
@router.post("/forgot-password")
async def forgot_password(body: PasswordResetRequest, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        # 安全策略：不暴露用户是否存在
        return {"message": "如果该邮箱已注册，重置邮件已发送"}

    token = await create_email_verification(db, body.email, action="reset_password", expire_hours=1)
    base_url = str(request.base_url).rstrip("/")
    reset_url = f"{base_url}/reset-password?email={body.email}&token={token}"

    if settings.SMTP_HOST:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(
            f"<h2>AgentPaperDistiller 密码重置</h2>"
            f"<p>请点击以下链接重置密码：</p>"
            f'<p><a href="{reset_url}">{reset_url}</a></p>'
            f"<p>链接 1 小时内有效。</p>",
            "html",
        )
        msg["Subject"] = "AgentPaperDistiller — 密码重置"
        msg["From"] = settings.SMTP_FROM_EMAIL
        msg["To"] = body.email
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        print(f"[DEV] 密码重置链接: {reset_url}")

    return {"message": "如果该邮箱已注册，重置邮件已发送"}


# ---------------------------------------------------------------------------
# POST /api/auth/reset-password
# ---------------------------------------------------------------------------
@router.post("/reset-password")
async def reset_password(body: PasswordResetConfirm, db: AsyncSession = Depends(get_db)):
    ok = await verify_email_token(db, body.email, body.token, action="reset_password")
    if not ok:
        raise HTTPException(status_code=400, detail="重置链接无效或已过期")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.hashed_password = hash_password(body.new_password)
    user.password_reset_token = None
    user.password_reset_expires = None
    await db.flush()
    return {"message": "密码重置成功"}


# ---------------------------------------------------------------------------
# GET /api/auth/users  (admin only)
# ---------------------------------------------------------------------------
@router.get("/users", response_model=list[UserResponse])
async def list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.id))
    return [UserResponse.model_validate(u) for u in result.scalars().all()]


# ---------------------------------------------------------------------------
# PUT /api/auth/users/{user_id}/role  (admin only)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# PUT /api/auth/users/{user_id}/status  (admin only)
# ---------------------------------------------------------------------------
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
