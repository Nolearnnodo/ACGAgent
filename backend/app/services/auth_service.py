"""鉴权服务。"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.deps import get_valid_refresh_session
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    get_token_subject,
    verify_password,
)
from app.models.auth import AuthSession
from app.models.user import User


class AuthService:
    """处理注册、登录、登出、刷新令牌等逻辑。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def register(self, email: str, password: str) -> User:
        """注册新用户。"""

        existing = self.db.query(User).filter(User.email == email).first()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该邮箱已注册。")

        user = User(email=email, password_hash=get_password_hash(password), role="user")
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def authenticate(self, email: str, password: str) -> User:
        """校验登录信息。"""

        user = self.db.query(User).filter(User.email == email).first()
        if user is None or not verify_password(password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误。")
        return user

    def create_token_pair(self, user: User) -> tuple[str, str]:
        """为用户创建 access / refresh token，并持久化 refresh session。"""

        access_token = create_access_token(subject=str(user.id), role=user.role)
        refresh_token, refresh_jti = create_refresh_token(subject=str(user.id))

        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        auth_session = AuthSession(user_id=user.id, refresh_jti=refresh_jti, expires_at=expires_at)
        self.db.add(auth_session)
        self.db.commit()

        return access_token, refresh_token

    def refresh_tokens(self, refresh_token: str) -> tuple[User, str, str]:
        """用 refresh token 刷新 access / refresh token。"""

        try:
            payload = get_token_subject(refresh_token, expected_type="refresh")
        except JWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新令牌无效。") from exc

        refresh_jti = payload.get("jti")
        if not refresh_jti:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新令牌无效。")

        current_session = get_valid_refresh_session(refresh_jti, self.db)
        current_session.revoked_at = datetime.now(timezone.utc)

        user = self.db.get(User, int(payload["sub"]))
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在。")

        access_token, new_refresh_token = self.create_token_pair(user)
        self.db.add(current_session)
        self.db.commit()
        return user, access_token, new_refresh_token

    def logout(self, refresh_token: str) -> None:
        """让 refresh session 失效。"""

        try:
            payload = get_token_subject(refresh_token, expected_type="refresh")
        except JWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新令牌无效。") from exc

        refresh_jti = payload.get("jti")
        session = get_valid_refresh_session(refresh_jti, self.db)
        session.revoked_at = datetime.now(timezone.utc)
        self.db.add(session)
        self.db.commit()
