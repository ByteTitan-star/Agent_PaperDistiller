import datetime as dt
import uuid

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EmailVerification, User


async def create_email_verification(
    db: AsyncSession,
    email: str,
    action: str = "register",
    expire_hours: int = 24,
) -> str:
    token = uuid.uuid4().hex
    record = EmailVerification(
        email=email,
        token=token,
        action=action,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=expire_hours),
    )
    db.add(record)
    await db.flush()
    return token


async def verify_email_token(
    db: AsyncSession,
    email: str,
    token: str,
    action: str = "register",
) -> bool:
    now = dt.datetime.now(dt.timezone.utc)
    result = await db.execute(
        select(EmailVerification).where(
            and_(
                EmailVerification.email == email,
                EmailVerification.token == token,
                EmailVerification.action == action,
                EmailVerification.used == False,
                EmailVerification.expires_at > now,
            )
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        return False
    record.used = True
    await db.flush()

    if action == "register":
        user_result = await db.execute(select(User).where(User.email == email))
        user = user_result.scalar_one_or_none()
        if user:
            user.email_verified = True
            await db.flush()

    return True


async def send_verification_email(email: str, token: str, base_url: str = "http://localhost:5173") -> None:
    """发送验证邮件。如果 SMTP 未配置，仅打印日志（开发模式）。"""
    from ..config import get_settings

    settings = get_settings()
    verify_url = f"{base_url}/verify-email?email={email}&token={token}"

    if not settings.SMTP_HOST:
        print(f"[DEV] 验证链接: {verify_url}")
        return

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(
        f"<h2>AgentPaperDistiller 邮箱验证</h2>"
        f"<p>请点击以下链接完成验证：</p>"
        f'<p><a href="{verify_url}">{verify_url}</a></p>'
        f"<p>链接 24 小时内有效。</p>",
        "html",
    )
    msg["Subject"] = "AgentPaperDistiller — 邮箱验证"
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = email

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
