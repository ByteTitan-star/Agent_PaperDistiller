from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth.dependencies import get_current_user
from ..dependencies import broker
from ..models import User

router = APIRouter(tags=["tasks"])

@router.get("/tasks/{task_id}")
async def get_task(task_id: str, user: User = Depends(get_current_user)) -> dict:
    state = await broker.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return state.model_dump()

@router.get("/tasks/{task_id}/events")
async def task_events(task_id: str, user: User = Depends(get_current_user)):
    state = await broker.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="任务不存在。")
    return StreamingResponse(
        broker.subscribe(task_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )