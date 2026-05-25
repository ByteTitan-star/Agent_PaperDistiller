"""
    定义了系统各个接口交互的Pydantic数据模型
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

ResultKind = Literal["translation", "summary", "improvement"]
TaskStatus = Literal["queued", "parsing", "translating", "summarizing", "critiquing", "done", "failed"]


class UploadResponse(BaseModel):
    task_id: str
    paper_id: str


class TemplateInfo(BaseModel):
    name: str


class PaperMeta(BaseModel):
    paper_id: str
    title: str
    source_filename: str
    created_at: str
    target_language: str
    summary_template: str = "tinghua.md"
    status: str
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    domain_tags: list[str] = Field(default_factory=list)


class TaskState(BaseModel):
    task_id: str
    paper_id: str
    status: TaskStatus
    progress: int
    message: str
    generation_model_name: str | None = None
    evaluation_model_name: str | None = None
    collaboration_mode: str | None = None
    updated_at: str


class ContentResponse(BaseModel):
    paper_id: str
    kind: ResultKind
    content: str


class ChatRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=2000,
        description="用户问题文本，用于向量检索与回答生成。",
    )
    top_k: int = Field(
        default=8,
        ge=1,
        le=20,
        description="检索返回上下文数量。",
    )
    deep_search: bool = Field(
        default=False,
        description="是否启用 ReAct 深度搜索模式（手动触发）。",
    )


class ChatResponse(BaseModel):
    answer: str
    contexts: list[str]
    reasoning_trace: str | None = None
    thinking_chain: list[str] | None = None


class SystemInfoResponse(BaseModel):
    app_name: str
    model_provider: str
    llm_model_name: str
    generation_model_name: str
    evaluation_model_name: str
    collaboration_mode: str
    embedding_model_name: str
    pipeline_mode: str
    app_version: str = "V2.0"
    app_update_date: str = ""
    app_author: str = ""
    app_changelog: str = ""


# ==================== Auth Schemas ====================

class SendCodeRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=100)


class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class RegisterFinal(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=6, max_length=6)


class UserRegister(BaseModel):
    email: EmailStr
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    role: str
    is_active: bool
    email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class EmailVerifyRequest(BaseModel):
    email: EmailStr
    token: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    email: EmailStr
    token: str
    new_password: str = Field(min_length=6, max_length=128)


class ResendVerifyRequest(BaseModel):
    email: EmailStr
