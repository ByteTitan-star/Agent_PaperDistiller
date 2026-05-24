import uvicorn


if __name__ == "__main__":
    # 本地开发入口：启动 FastAPI 应用并开启热重载。
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
