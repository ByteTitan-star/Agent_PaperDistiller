from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..auth.dependencies import get_current_user
from ..auth.jwt_utils import decode_access_token
from ..database import get_db
from ..dependencies import broker
from ..models import User

router = APIRouter(tags=["tasks"])
_bearer = HTTPBearer(auto_error=False)


async def _get_user_for_sse(
    token: str | None = Query(default=None, alias="token"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db=Depends(get_db),
) -> User:
    raw = token or (creds.credentials if creds else None)
    if not raw:
        raise HTTPException(403, "Not authenticated")
    payload = decode_access_token(raw)
    if not payload:
        raise HTTPException(403, "Invalid token")
    from sqlalchemy import select
    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(403, "用户不存在或已禁用")
    return user


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: User = Depends(get_current_user)) -> dict:
    state = await broker.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return state.model_dump()

@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, user: User = Depends(_get_user_for_sse)):
    state = await broker.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return StreamingResponse(
        broker.subscribe(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )