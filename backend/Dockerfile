# 后端镜像：Python 3.11 + FastAPI + uvicorn + alembic
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 系统依赖：
# - libxml2 / libxslt1.1：python-docx / openpyxl 等可能用到
# - libmagic1：markitdown 探测 mime 用
# - build-essential：少量纯 Python 包仍带 C 扩展
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2 \
        libxslt1.1 \
        libmagic1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖（利用 layer cache）
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt && \
    pip install "bcrypt==4.0.1" --force-reinstall

# 镜像里包一份后端代码；docker-compose 同时挂载源码以便开发热更
COPY backend /app/backend

WORKDIR /app/backend

EXPOSE 8000

# 启动前自动跑迁移，再起 API。
# Neo4j 不可用时 alembic 仍可跑（只动 SQLite），uvicorn 则按业务调用 graph repository。
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
