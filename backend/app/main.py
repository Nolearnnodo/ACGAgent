"""FastAPI 应用入口。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import auth, chat, health, passages, users
from app.core.config import get_settings
from app.db import base  # noqa: F401
from app.db.base_class import Base
from app.db.session import engine

settings = get_settings()


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="面向图数据库维护场景的可扩展 Agent 系统。",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def on_startup() -> None:
        """启动时初始化数据库表。

        当前项目仍处于初始阶段，直接建表足够；后续可切换为 Alembic 迁移。
        """

        Base.metadata.create_all(bind=engine)

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(passages.router, prefix="/api/v1")

    return app


app = create_app()
