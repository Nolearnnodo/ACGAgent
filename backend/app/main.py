"""FastAPI 应用入口。"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routers import auth, chat, health, passages, users
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.skills.registry import SkillRegistry

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

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(passages.router, prefix="/api/v1")

    @app.on_event("startup")
    def _sync_skill_metadata() -> None:
        """启动时把代码中的 Skill 定义同步进 SQLite，
        避免 ConversationService 懒加载导致 metadata 漂移到旧版本。"""

        with SessionLocal() as db:
            SkillRegistry().register_builtin_metadata(db)

    return app


app = create_app()
