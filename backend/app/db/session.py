"""数据库会话管理。"""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()

# SQLite 数据文件默认放在 data 目录下，启动时自动创建目录。
if settings.sqlite_database_url.startswith("sqlite:///./"):
    database_file = settings.sqlite_database_url.replace("sqlite:///./", "", 1)
    Path(database_file).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.sqlite_database_url,
    connect_args={"check_same_thread": False} if settings.sqlite_database_url.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI 依赖项：按请求提供数据库会话。"""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
