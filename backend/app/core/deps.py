"""FastAPI 依赖注入。

这里统一处理当前登录用户、管理员权限等依赖，避免在路由中重复写鉴权逻辑。
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.security import get_token_subject
from app.db.session import get_db
from app.models.auth import AuthSession
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """解析访问令牌并返回当前用户。"""

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或过期的访问令牌。",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = get_token_subject(token, expected_type="access")
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError as exc:
        raise credentials_exception from exc

    user = db.get(User, int(user_id))
    if user is None:
        raise credentials_exception

    return user


def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """仅允许管理员访问。"""

    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可访问此资源。")
    return current_user


def get_valid_refresh_session(refresh_jti: str, db: Session) -> AuthSession:
    """根据 refresh token 的 jti 获取仍有效的会话。"""

    session = db.query(AuthSession).filter(AuthSession.refresh_jti == refresh_jti).first()
    if session is None or session.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新会话无效。")
    return session
