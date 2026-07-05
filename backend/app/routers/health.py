from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """健康检查接口。

    前端页面：无直接页面调用
    用途：运维监控 / 部署探针，确认后端服务存活
    """
    return {"status": "ok"}
