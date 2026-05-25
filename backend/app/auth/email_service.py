import datetime as dt
import random
import string

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import EmailVerification, User


def _generate_code(length: int = 6) -> str:
    """生成纯数字验证码。"""
    return "".join(random.choices(string.digits, k=length))


async def create_email_code(
    db: AsyncSession,
    email: str,
    action: str = "register",
    expire_minutes: int = 10,
) -> str:
    """生成 6 位验证码并存入数据库，返回验证码明文（用于发送）。"""
    code = _generate_code(6)
    record = EmailVerification(
        email=email,
        token=code,
        action=action,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=expire_minutes),
    )
    db.add(record)
    await db.flush()
    return code


async def check_email_code(
    db: AsyncSession,
    email: str,
    code: str,
    action: str = "register",
) -> bool:
    """仅校验验证码是否正确，不消费（用于前端预校验）。"""
    now = dt.datetime.now(dt.timezone.utc)
    result = await db.execute(
        select(EmailVerification).where(
            and_(
                EmailVerification.email == email,
                EmailVerification.token == code,
                EmailVerification.action == action,
                EmailVerification.used == False,
                EmailVerification.expires_at > now,
            )
        )
    )
    return result.scalar_one_or_none() is not None


async def verify_email_code(
    db: AsyncSession,
    email: str,
    code: str,
    action: str = "register",
) -> bool:
    now = dt.datetime.now(dt.timezone.utc)
    result = await db.execute(
        select(EmailVerification).where(
            and_(
                EmailVerification.email == email,
                EmailVerification.token == code,
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
            user.is_active = True
            await db.flush()

    return True


async def send_code_email(email: str, code: str) -> None:
    """发送验证码邮件。"""
    from ..config import get_settings

    settings = get_settings()

    # 未配置 SMTP 时打印验证码到控制台（开发调试用）
    if not settings.SMTP_HOST:
        print(f"[验证码] {code} → {email}")
        return

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(
        f"<div style='font-family:sans-serif;max-width:400px;margin:0 auto'>"
        f"<h2 style='color:#7c3aed'>AgentPaperDistiller</h2>"
        f"<p>您的验证码是：</p>"
        f"<p style='font-size:32px;font-weight:700;letter-spacing:8px;color:#7c3aed'>{code}</p>"
        f"<p style='color:#64748b;font-size:13px'>验证码 10 分钟内有效。</p>"
        f"</div>",
        "html",
    )
    msg["Subject"] = "AgentPaperDistiller — 验证码"
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = email

    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, 465) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
