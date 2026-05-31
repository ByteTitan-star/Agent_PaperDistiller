import copy
import json
import logging
import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.crypto import aes_decrypt
from ..auth.dependencies import get_current_user
from ..auth.jwt_utils import decode_access_token
from ..config import get_settings
from ..database import get_db
from ..dependencies import broker, storage
from ..models import ChatMessage, ChatSession, Paper, TaskRecord, User, UserApiConfig
from ..schemas import ChatRequest, ChatResponse, ContentResponse, PaperMeta
from ..services.chat import chat_with_paper, chat_with_paper_stream
from ..storage import domain_tag_from_template, unique_keep_order

router = APIRouter(tags=["papers"])
_bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger("papers")


async def _get_user_for_embedded(
    token: str | None = Query(default=None, alias="token"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    """支持 Authorization header 和 ?token= 查询参数两种认证方式。
    用于 iframe/PDF 等无法发送 header 的场景。"""
    raw = token or (creds.credentials if creds else None)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_access_token(raw)
    if not payload:
        raise HTTPException(status_code=401, detail="无效或过期的 Token")
    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return user


class PaginatedPapers(BaseModel):
    items: list[PaperMeta]
    total: int
    page: int
    page_size: int
    total_pages: int


async def _load_user_chat_settings(user: User, db: AsyncSession):
    """从数据库加载用户 API 配置，返回带用户 key 的 Settings 副本。"""
    result = await db.execute(
        select(UserApiConfig).where(UserApiConfig.user_id == user.id)
    )
    config = result.scalar_one_or_none()

    base_settings = get_settings()

    if config is None:
        return base_settings

    def _decrypt(val: str | None) -> str | None:
        if not val:
            return None
        try:
            return aes_decrypt(val)
        except Exception:
            return val

    user_settings = copy.deepcopy(base_settings)
    ds_key = _decrypt(config.deepseek_api_key)
    if ds_key:
        user_settings.deepseek_api_key = ds_key
    if config.deepseek_base_url:
        user_settings.deepseek_base_url = config.deepseek_base_url
    qwen_key = _decrypt(config.qwen_api_key)
    if qwen_key:
        user_settings.qwen_api_key = qwen_key
    if config.qwen_base_url:
        user_settings.qwen_base_url = config.qwen_base_url
    tavily_key = _decrypt(config.tavily_api_key)
    if tavily_key:
        user_settings.tavily_api_key = tavily_key
    return user_settings


def _paper_to_meta(paper: Paper, task_id: str | None = None) -> PaperMeta:
    template_tag = domain_tag_from_template(paper.summary_template)
    domain_tags = unique_keep_order([template_tag, *(paper.domain_tags or [])])
    return PaperMeta(
        paper_id=paper.paper_id,
        title=paper.title,
        source_filename=paper.source_filename or "",
        created_at=paper.created_at.isoformat(),
        target_language=paper.target_language,
        summary_template=paper.summary_template,
        status=paper.status,
        year=paper.year,
        authors=paper.authors or [],
        domain_tags=domain_tags,
        task_id=task_id,
    )


async def _get_user_paper(
    paper_id: str, user: User, db: AsyncSession
) -> Paper:
    result = await db.execute(select(Paper).where(Paper.paper_id == paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(status_code=404, detail="论文不存在。")
    if paper.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="无权访问此论文。")
    return paper


@router.get("/papers", response_model=PaginatedPapers)
async def list_papers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    base_query = select(Paper)
    if user.role != "admin":
        base_query = base_query.where(Paper.user_id == user.id)

    # 总数
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 分页数据
    offset = (page - 1) * page_size
    result = await db.execute(
        base_query.order_by(Paper.created_at.desc()).offset(offset).limit(page_size)
    )
    papers = result.scalars().all()

    # 查询这些论文中仍在处理的任务
    paper_ids = [p.paper_id for p in papers]
    active_statuses = ["queued", "parsing", "translating", "summarizing", "critiquing"]
    task_map: dict[str, str] = {}
    if paper_ids:
        task_q = await db.execute(
            select(TaskRecord.paper_id, TaskRecord.task_id).where(
                TaskRecord.paper_id.in_(paper_ids),
                TaskRecord.status.in_(active_statuses),
            )
        )
        task_map = {row[0]: row[1] for row in task_q.all()}

    items = [_paper_to_meta(p, task_id=task_map.get(p.paper_id)) for p in papers]

    return PaginatedPapers(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.get("/papers/{paper_id}", response_model=PaperMeta)
async def get_paper(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    active_statuses = ["queued", "parsing", "translating", "summarizing", "critiquing"]
    task_q = await db.execute(
        select(TaskRecord.task_id).where(
            TaskRecord.paper_id == paper_id,
            TaskRecord.status.in_(active_statuses),
        ).limit(1)
    )
    task_id_row = task_q.scalar_one_or_none()
    return _paper_to_meta(paper, task_id=task_id_row)


@router.get("/papers/{paper_id}/content/{kind}", response_model=ContentResponse)
async def get_content(
    paper_id: str,
    kind: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if kind not in {"translation", "summary", "improvement"}:
        raise HTTPException(status_code=400, detail="不支持的内容类型。")

    paper = await _get_user_paper(paper_id, user, db)
    summary_template = paper.summary_template if kind == "summary" else None
    content = storage.read_result(paper_id, kind, summary_template=summary_template)  # type: ignore[arg-type]
    return ContentResponse(paper_id=paper_id, kind=kind, content=content)  # type: ignore[arg-type]


@router.get("/papers/{paper_id}/pdf")
async def get_pdf(
    paper_id: str,
    user: User = Depends(_get_user_for_embedded),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_paper(paper_id, user, db)

    # 优先使用 OSS 签名 URL（302 重定向）
    oss_url = storage.oss_pdf_signed_url(paper_id, expires=3600)
    if oss_url:
        return RedirectResponse(url=oss_url)

    pdf_path = storage.pdf_path(paper_id)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/papers/{paper_id}/pdf/download")
async def download_pdf(
    paper_id: str,
    user: User = Depends(_get_user_for_embedded),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    pdf_path = storage.pdf_path(paper_id)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF 文件不存在。")

    filename = paper.source_filename or f"{paper_id}.pdf"
    return FileResponse(pdf_path, media_type="application/pdf", filename=filename)


@router.get("/papers/{paper_id}/translation/layout")
async def get_translation_layout(
    paper_id: str,
    user: User = Depends(_get_user_for_embedded),
    db: AsyncSession = Depends(get_db),
):
    await _get_user_paper(paper_id, user, db)
    layout_path = storage.paper_output_dir(paper_id) / "translated_layout.html"
    if not layout_path.exists():
        raise HTTPException(status_code=404, detail="双栏翻译版尚未生成。")
    return FileResponse(
        layout_path,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": "inline"},
    )


async def _get_or_create_session(db: AsyncSession, user_id: int, paper_id: str, session_id: str | None) -> tuple[ChatSession, list[dict[str, str]]]:
    """获取或创建会话，返回 (session, history_messages)。"""
    if session_id:
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.session_id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        session = result.scalar_one_or_none()
        if session:
            # 加载历史消息
            msg_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            )
            history = [{"role": m.role, "content": m.content} for m in msg_result.scalars()]
            return session, history

    # 创建新会话
    new_session_id = session_id or uuid.uuid4().hex[:16]
    session = ChatSession(session_id=new_session_id, user_id=user_id, paper_id=paper_id)
    db.add(session)
    await db.flush()
    return session, []


async def _save_message(db: AsyncSession, session_id: str, role: str, content: str,
                        deep_search: bool = False, thinking_chain: list | None = None,
                        contexts: list | None = None, token_usage: dict | None = None):
    """保存一条消息到数据库。"""
    msg = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        deep_search=deep_search,
        thinking_chain=thinking_chain,
        contexts=contexts,
        token_usage=token_usage,
    )
    db.add(msg)


@router.post("/papers/{paper_id}/chat", response_model=ChatResponse)
async def chat(
    paper_id: str,
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    user_settings = await _load_user_chat_settings(user, db)

    session, history = await _get_or_create_session(db, user.id, paper_id, payload.session_id)
    await _save_message(db, session.session_id, "user", payload.question, deep_search=payload.deep_search)
    await db.commit()

    result = await chat_with_paper(
        paper_id, payload, storage,
        summary_template=paper.summary_template,
        settings=user_settings,
        user_id=user.id,
        history=history,
    )

    await _save_message(db, session.session_id, "assistant", result.answer,
                        deep_search=payload.deep_search,
                        thinking_chain=result.thinking_chain,
                        contexts=result.contexts)
    await db.commit()

    result.session_id = session.session_id
    return result


@router.post("/papers/{paper_id}/chat/stream")
async def chat_stream(
    paper_id: str,
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)
    user_settings = await _load_user_chat_settings(user, db)

    session, history = await _get_or_create_session(db, user.id, paper_id, payload.session_id)
    await _save_message(db, session.session_id, "user", payload.question, deep_search=payload.deep_search)
    await db.commit()

    collected_answer = []
    collected_thinking = []

    async def event_generator():
        async for event in chat_with_paper_stream(
            paper_id, payload, storage,
            summary_template=paper.summary_template,
            settings=user_settings,
            user_id=user.id,
            history=history,
        ):
            # 从 SSE 事件中收集 answer 用于持久化
            if "token" in event:
                try:
                    data = json.loads(event.split("data: ")[1].strip())
                    if data.get("type") == "token":
                        collected_answer.append(data.get("text", ""))
                except Exception:
                    pass
            elif "done" in event:
                try:
                    data = json.loads(event.split("data: ")[1].strip())
                    if data.get("type") == "done":
                        # 注入 session_id 到 done 事件
                        data["session_id"] = session.session_id
                        event = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                        if data.get("thinking_chain"):
                            collected_thinking.extend(data["thinking_chain"])
                except Exception:
                    pass
            yield event

        # 流结束后保存 assistant 消息
        answer_text = "".join(collected_answer)
        if answer_text:
            from ..database import async_session_factory
            async with async_session_factory() as save_db:
                await _save_message(save_db, session.session_id, "assistant", answer_text,
                                    deep_search=payload.deep_search,
                                    thinking_chain=collected_thinking or None)
                await save_db.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.delete("/papers/{paper_id}")
async def delete_paper(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    paper = await _get_user_paper(paper_id, user, db)

    # 取消正在运行的任务
    active_statuses = ["queued", "parsing", "translating", "summarizing", "critiquing"]
    task_result = await db.execute(
        select(TaskRecord).where(
            TaskRecord.paper_id == paper_id,
            TaskRecord.status.in_(active_statuses),
        )
    )
    for task in task_result.scalars().all():
        await broker.cancel(task.task_id)
        task.status = "cancelled"
        task.message = "用户删除论文，任务已取消"

    # 删除磁盘文件
    try:
        import shutil
        from pathlib import Path
        output_dir = storage.paper_output_dir(paper_id)
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raw_pdf = storage.base_dir / "raw" / f"{paper_id}.pdf"
        if raw_pdf.exists():
            raw_pdf.unlink()
    except Exception:
        pass

    await db.delete(paper)
    return {"message": "论文已删除"}
