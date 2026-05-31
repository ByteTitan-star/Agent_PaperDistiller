"""
FastAPI 应用入口
"""
import os
os.environ["HF_HUB_OFFLINE"] = "1"

# 加载 .env 文件到 os.environ（pydantic-settings 的 load_dotenv 不会注入 os.environ）
from dotenv import load_dotenv
_env = os.getenv("APP_ENV", "dev")
load_dotenv(f".env.{_env}", encoding="utf-8")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings    # 绝对导入（需确保 backend 在 Python 路径中）
from app.routers import health, system, templates, upload, tasks, papers

settings = get_settings()

app = FastAPI(title=settings.app_name)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(system.router, prefix=settings.api_prefix)
app.include_router(templates.router, prefix=settings.api_prefix)
app.include_router(upload.router, prefix=settings.api_prefix)
app.include_router(tasks.router, prefix=settings.api_prefix)
app.include_router(papers.router, prefix=settings.api_prefix)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)