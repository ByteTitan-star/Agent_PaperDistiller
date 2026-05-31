"""Chat history endpoints — 会话列表、消息历史、删除会话。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..database import get_db
from ..models import ChatMessage, ChatSession, User
from ..schemas import ChatMessageInfo, ChatSessionInfo

router = APIRouter(tags=["chat-history"])


@router.get("/papers/{paper_id}/chat/sessions", response_model=list[ChatSessionInfo])
async def list_chat_sessions(
    paper_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户在某论文下的所有会话。"""
    stmt = (
        select(ChatSession)
        .where(ChatSession.paper_id == paper_id, ChatSession.user_id == user.id)
        .order_by(ChatSession.created_at.desc())
    )
    result = await db.execute(stmt)
    sessions = result.scalars().all()

    infos = []
    for s in sessions:
        # 统计消息数 + 最后一条消息预览
        count_result = await db.execute(
            select(func.count(ChatMessage.id)).where(ChatMessage.session_id == s.session_id)
        )
        count = count_result.scalar() or 0

        last_msg_result = await db.execute(
            select(ChatMessage.content)
            .where(ChatMessage.session_id == s.session_id, ChatMessage.role == "assistant")
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )
        last_preview = last_msg_result.scalar()
        if last_preview:
            last_preview = last_preview[:80]

        infos.append(ChatSessionInfo(
            session_id=s.session_id,
            paper_id=s.paper_id,
            created_at=str(s.created_at),
            message_count=count,
            last_message_preview=last_preview,
        ))
    return infos


@router.get("/chat/sessions/{session_id}/messages", response_model=list[ChatMessageInfo])
async def get_chat_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取某个会话的完整消息历史。"""
    # 验证所有权
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == user.id,
        )
    )
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    msg_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    return [
        ChatMessageInfo(
            role=m.role,
            content=m.content,
            thinking_chain=m.thinking_chain if isinstance(m.thinking_chain, list) else None,
            deep_search=m.deep_search,
            created_at=str(m.created_at),
        )
        for m in msg_result.scalars()
    ]


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除一个会话及其所有消息。"""
    session_result = await db.execute(
        select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.user_id == user.id,
        )
    )
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    await db.delete(session)
    await db.commit()
    return {"ok": True}
