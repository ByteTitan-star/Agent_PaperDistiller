# ==========================================
# AgentPaperDistiller - Multi-stage Dockerfile
# ==========================================

# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Backend runtime
FROM python:3.11-slim AS backend

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python 依赖（利用 Docker 缓存层）
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY backend/ ./

# 复制前端构建产物到后端静态目录
COPY --from=frontend-build /app/frontend/dist ./static

# 下载嵌入模型（sentence-transformers）
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='./models')"

# 创建数据目录
RUN mkdir -p data logs

# 暴露端口
EXPOSE 8001

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8001/api/health || exit 1

# 启动命令
CMD ["python", "main.py"]
