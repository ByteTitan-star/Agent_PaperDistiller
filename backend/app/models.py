import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# 1. 用户表
# ---------------------------------------------------------------------------
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("user", "admin", name="user_role_enum"), nullable=False, default="user"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_verify_token: Mapped[str | None] = mapped_column(String(128), default=None)
    password_reset_token: Mapped[str | None] = mapped_column(String(128), default=None)
    password_reset_expires: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)

    papers = relationship("Paper", back_populates="owner", lazy="selectin")
    api_config = relationship("UserApiConfig", back_populates="user", uselist=False, lazy="selectin")
    chat_sessions = relationship("ChatSession", back_populates="user", lazy="selectin")


# ---------------------------------------------------------------------------
# 2. 论文表
# ---------------------------------------------------------------------------
class Paper(Base, TimestampMixin):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(500), default=None)
    status: Mapped[str] = mapped_column(
        Enum("processing", "completed", "failed", name="paper_status_enum"),
        nullable=False,
        default="processing",
    )
    target_language: Mapped[str] = mapped_column(String(50), nullable=False, default="Chinese")
    summary_template: Mapped[str] = mapped_column(String(200), nullable=False, default="tinghua.md")
    year: Mapped[int | None] = mapped_column(Integer, default=None)
    authors: Mapped[dict | None] = mapped_column(JSON, default=None)
    domain_tags: Mapped[dict | None] = mapped_column(JSON, default=None)

    pdf_path: Mapped[str | None] = mapped_column(String(1000), default=None)
    output_dir: Mapped[str | None] = mapped_column(String(1000), default=None)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    owner = relationship("User", back_populates="papers")


# ---------------------------------------------------------------------------
# 3. 模板表
# ---------------------------------------------------------------------------
class Template(Base, TimestampMixin):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    domain_tag: Mapped[str] = mapped_column(String(100), default="General")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, default=None)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    owner = relationship("User", backref="templates")

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_templates_user_name"),
        Index("idx_templates_user", "user_id"),
    )


# ---------------------------------------------------------------------------
# 4. 任务记录表
# ---------------------------------------------------------------------------
class TaskRecord(Base, TimestampMixin):
    __tablename__ = "task_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    paper_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    generation_model: Mapped[str | None] = mapped_column(String(100), default=None)
    evaluation_model: Mapped[str | None] = mapped_column(String(100), default=None)
    collaboration_mode: Mapped[str | None] = mapped_column(String(200), default=None)


# ---------------------------------------------------------------------------
# 5. 用户 API 配置表
# ---------------------------------------------------------------------------
class UserApiConfig(Base):
    __tablename__ = "user_api_configs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    deepseek_api_key: Mapped[str | None] = mapped_column(String(500), default=None)
    deepseek_base_url: Mapped[str | None] = mapped_column(String(500), default=None)
    qwen_api_key: Mapped[str | None] = mapped_column(String(500), default=None)
    qwen_base_url: Mapped[str | None] = mapped_column(String(500), default=None)
    tavily_api_key: Mapped[str | None] = mapped_column(String(500), default=None)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="api_config")


# ---------------------------------------------------------------------------
# 6. 系统配置表
# ---------------------------------------------------------------------------
class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    setting_value: Mapped[str | None] = mapped_column(Text, default=None)
    setting_type: Mapped[str] = mapped_column(
        Enum("string", "int", "float", "bool", "json", name="setting_type_enum"),
        nullable=False,
        default="string",
    )
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# 7. 对话会话表
# ---------------------------------------------------------------------------
class ChatSession(Base, TimestampMixin):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    paper_id: Mapped[str] = mapped_column(String(100), nullable=False)

    user = relationship("User", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage", back_populates="session", lazy="selectin", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# 8. 对话消息表
# ---------------------------------------------------------------------------
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.session_id"), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("user", "assistant", "system", name="chat_role_enum"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    thinking_chain: Mapped[dict | None] = mapped_column(JSON, default=None)
    contexts: Mapped[dict | None] = mapped_column(JSON, default=None)
    deep_search: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    token_usage: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("idx_chat_messages_session", "session_id"),
        Index("idx_chat_messages_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# 9. 审计日志表
# ---------------------------------------------------------------------------
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50), default=None)
    resource_id: Mapped[str | None] = mapped_column(String(200), default=None)
    detail: Mapped[dict | None] = mapped_column(JSON, default=None)
    ip_address: Mapped[str | None] = mapped_column(String(45), default=None)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_audit_user", "user_id"),
        Index("idx_audit_action", "action"),
        Index("idx_audit_resource", "resource_type", "resource_id"),
        Index("idx_audit_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# 10. Token 用量记录表
# ---------------------------------------------------------------------------
class TokenUsageLog(Base):
    __tablename__ = "token_usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False, default="chat")
    detail: Mapped[dict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_token_user", "user_id"),
        Index("idx_token_model", "model_name"),
        Index("idx_token_action", "action_type"),
        Index("idx_token_created", "created_at"),
    )


# ---------------------------------------------------------------------------
# 11. 邮箱验证记录表
# ---------------------------------------------------------------------------
class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(
        Enum("register", "reset_password", name="email_action_enum"),
        nullable=False,
        default="register",
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_email_token", "email", "token"),
        Index("idx_email_expires", "expires_at"),
    )
