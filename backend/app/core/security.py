"""安全相关工具。

集中放置密码散列、JWT 生成与解析逻辑，避免散落到各层。
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与哈希值是否匹配。"""

    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """生成密码哈希。"""

    return pwd_context.hash(password)


def create_access_token(subject: str, role: str) -> str:
    """生成访问令牌。"""

    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {"sub": subject, "role": role, "type": "access", "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, session_jti: str | None = None) -> tuple[str, str]:
    """生成刷新令牌，并返回令牌与 jti。"""

    expire = datetime.now(timezone.utc) + timedelta(days=settings.jwt_refresh_token_expire_days)
    jti = session_jti or str(uuid4())
    payload = {"sub": subject, "type": "refresh", "jti": jti, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm), jti


def decode_token(token: str) -> dict:
    """解析 JWT；无效时抛出异常供上层处理。"""

    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def get_token_subject(token: str, expected_type: str) -> dict:
    """校验令牌类型后返回负载。"""

    payload = decode_token(token)
    if payload.get("type") != expected_type:
        raise JWTError("token type mismatch")
    return payload
