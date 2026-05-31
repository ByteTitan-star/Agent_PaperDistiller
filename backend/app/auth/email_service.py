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


# ---------------------------------------------------------------------------
# 通知类邮件
# ---------------------------------------------------------------------------
async def _send_html_email(to_email: str, subject: str, html_body: str, text_body: str) -> None:
    """通用 HTML 邮件发送，未配置 SMTP 时打印到控制台。"""
    from ..config import get_settings

    settings = get_settings()

    if not settings.SMTP_HOST:
        print(f"[通知邮件] {subject} → {to_email}\n{text_body}")
        return

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email

    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, 465) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)


async def send_template_deleted_email(
    to_email: str, username: str | None, template_name: str
) -> None:
    """模板被管理员删除后通知模板所有者。"""
    name = username or "用户"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px;background:#f8fafc;border-radius:12px">
      <div style="text-align:center;margin-bottom:24px">
        <h2 style="color:#0f172a;margin:0">📄 模板删除通知</h2>
      </div>
      <div style="background:#fff;padding:24px;border-radius:8px;border:1px solid #e2e8f0">
        <p>Hi {name}，</p>
        <p>您上传的模板 <strong style="color:#dc2626">「{template_name}」</strong> 已被管理员删除。</p>
        <p>如有疑问，请联系平台管理员。</p>
      </div>
      <div style="text-align:center;margin-top:24px;color:#94a3b8;font-size:12px">
        <p>— AgentPaperDistiller Team</p>
      </div>
    </div>
    """
    await _send_html_email(
        to_email=to_email,
        subject=f"[AgentPaperDistiller] 模板删除通知 - {template_name}",
        html_body=html,
        text_body=f"您上传的模板「{template_name}」已被管理员删除。如有疑问请联系管理员。",
    )


async def send_account_deleted_email(to_email: str, username: str) -> None:
    """用户账号被管理员删除后通知该用户。"""
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px;background:#f8fafc;border-radius:12px">
      <div style="text-align:center;margin-bottom:24px">
        <h2 style="color:#0f172a;margin:0">⚠️ 账号删除通知</h2>
      </div>
      <div style="background:#fff;padding:24px;border-radius:8px;border:1px solid #e2e8f0">
        <p>Hi {username}，</p>
        <p>您的 <strong>AgentPaperDistiller</strong> 账号（{to_email}）已被管理员删除。</p>
        <p>您的所有数据（论文、模板、对话记录等）均已清除。</p>
        <p>如有疑问，请联系平台管理员。</p>
      </div>
      <div style="text-align:center;margin-top:24px;color:#94a3b8;font-size:12px">
        <p>— AgentPaperDistiller Team</p>
      </div>
    </div>
    """
    await _send_html_email(
        to_email=to_email,
        subject="[AgentPaperDistiller] 账号删除通知",
        html_body=html,
        text_body=f"您的 AgentPaperDistiller 账号（{to_email}）已被管理员删除。如有疑问请联系管理员。",
    )
