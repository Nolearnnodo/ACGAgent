"""统一配置层。

所有模块都从这里读取配置，避免在业务代码中直接访问环境变量。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    """项目运行配置。"""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    app_name: str = Field(default="ACGAgent Graph Maintenance System", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_cors_origins: str = Field(default="http://localhost:5173", alias="APP_CORS_ORIGINS")

    sqlite_database_url: str = Field(default="sqlite:///./data/acg_agent.sqlite3", alias="SQLITE_DATABASE_URL")

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field(default="please_change_me", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    jwt_secret_key: str = Field(default="please_change_this_secret", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(default=60, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, ge=1, le=65535, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from_email: str = Field(default="", alias="SMTP_FROM_EMAIL")
    smtp_from_name: str = Field(default="ACGAgent", alias="SMTP_FROM_NAME")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")
    smtp_use_ssl: bool = Field(default=False, alias="SMTP_USE_SSL")
    smtp_timeout_seconds: int = Field(default=10, gt=0, alias="SMTP_TIMEOUT_SECONDS")

    password_reset_code_expire_minutes: int = Field(
        default=10,
        gt=0,
        alias="PASSWORD_RESET_CODE_EXPIRE_MINUTES",
    )
    password_reset_code_cooldown_seconds: int = Field(
        default=60,
        ge=0,
        alias="PASSWORD_RESET_CODE_COOLDOWN_SECONDS",
    )
    password_reset_max_attempts: int = Field(default=5, gt=0, alias="PASSWORD_RESET_MAX_ATTEMPTS")

    conversation_memory_window: int = Field(default=8, alias="CONVERSATION_MEMORY_WINDOW")

    llm_provider: str = Field(default="mock", alias="LLM_PROVIDER")
    llm_model_name: str = Field(default="mock-planner", alias="LLM_MODEL_NAME")
    llm_api_base_url: str = Field(default="", alias="LLM_API_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_timeout_seconds: int = Field(default=120, alias="LLM_TIMEOUT_SECONDS")
    passage_upload_concurrency: int = Field(default=10, alias="PASSAGE_UPLOAD_CONCURRENCY")
    ai_annotation_concurrency: int = Field(default=1, ge=1, alias="AI_ANNOTATION_CONCURRENCY")
    identity_merge_disabled: bool = Field(default=False, alias="IDENTITY_MERGE_DISABLED")

    @property
    def cors_origins(self) -> list[str]:
        """把逗号分隔的域名字符串转换为列表。"""

        return [origin.strip() for origin in self.app_cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """缓存配置对象，避免重复解析 .env。"""

    return Settings()
