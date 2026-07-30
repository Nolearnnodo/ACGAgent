"""鉴权服务。"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from jose import JWTError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.deps import get_valid_refresh_session
from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    get_token_subject,
    verify_password,
)
from app.models.auth import AuthSession, PasswordResetCode
from app.models.user import User
from app.services.email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    EmailService,
)

logger = logging.getLogger(__name__)


class AuthService:
    """处理注册、登录、登出、刷新令牌等逻辑。"""

    password_reset_request_message = "如果该邮箱已注册，验证码将发送至邮箱。"
    invalid_reset_code_message = "验证码无效或已过期，请重新获取。"

    def __init__(
        self,
        db: Session,
        *,
        email_service: EmailService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self.email_service = email_service or EmailService(self.settings)

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

        expires_at = datetime.now(timezone.utc) + timedelta(days=self.settings.jwt_refresh_token_expire_days)
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

    def request_password_reset(self, email: str) -> str:
        """为已注册邮箱生成并发送一次性密码重置验证码。

        无论邮箱是否存在或是否处于发送冷却期，均返回相同文案。
        """

        try:
            self.email_service.ensure_configured()
        except EmailConfigurationError as exc:
            logger.error("密码重置邮件服务配置无效：%s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="邮件服务暂不可用，请稍后再试。",
            ) from exc

        user = self.db.query(User).filter(User.email == email).first()
        if user is None:
            return self.password_reset_request_message

        now = datetime.now(timezone.utc)
        cooldown_start = now - timedelta(seconds=self.settings.password_reset_code_cooldown_seconds)
        recent_code = (
            self.db.query(PasswordResetCode)
            .filter(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.consumed_at.is_(None),
                PasswordResetCode.created_at >= cooldown_start,
            )
            .order_by(PasswordResetCode.created_at.desc())
            .first()
        )
        if recent_code is not None:
            return self.password_reset_request_message

        # 新验证码生成后，旧验证码即使尚未过期也不再有效。
        self.db.query(PasswordResetCode).filter(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.consumed_at.is_(None),
        ).update({PasswordResetCode.consumed_at: now}, synchronize_session=False)

        code = str(secrets.randbelow(900_000) + 100_000)
        salt = secrets.token_hex(16)
        reset_code = PasswordResetCode(
            user_id=user.id,
            code_salt=salt,
            code_hash=self._hash_password_reset_code(code, salt),
            expires_at=now + timedelta(minutes=self.settings.password_reset_code_expire_minutes),
        )
        self.db.add(reset_code)
        self.db.commit()
        reset_code_id = reset_code.id

        try:
            self.email_service.send_password_reset_code(user.email, code)
        except (EmailConfigurationError, EmailDeliveryError) as exc:
            logger.warning("密码重置验证码发送失败：%s", exc)
            try:
                failed_code = self.db.get(PasswordResetCode, reset_code_id)
                if failed_code is not None:
                    self.db.delete(failed_code)
                    self.db.commit()
            except Exception:
                self.db.rollback()
                logger.exception("清理发送失败的密码重置验证码时发生异常。")
            # 邮件传输失败也返回统一文案，避免通过响应差异枚举已注册邮箱。
            return self.password_reset_request_message

        return self.password_reset_request_message

    def reset_password(self, email: str, code: str, new_password: str) -> None:
        """校验邮箱验证码并重置密码。"""

        user = self.db.query(User).filter(User.email == email).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=self.invalid_reset_code_message)

        now = datetime.now(timezone.utc)
        reset_code = (
            self.db.query(PasswordResetCode)
            .filter(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.consumed_at.is_(None),
                PasswordResetCode.expires_at > now,
                PasswordResetCode.failed_attempts < self.settings.password_reset_max_attempts,
            )
            .order_by(PasswordResetCode.created_at.desc())
            .first()
        )
        if reset_code is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=self.invalid_reset_code_message)

        submitted_hash = self._hash_password_reset_code(code, reset_code.code_salt)
        if not hmac.compare_digest(submitted_hash, reset_code.code_hash):
            reset_code.failed_attempts += 1
            if reset_code.failed_attempts >= self.settings.password_reset_max_attempts:
                reset_code.consumed_at = now
            self.db.add(reset_code)
            self.db.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=self.invalid_reset_code_message)

        user.password_hash = get_password_hash(new_password)
        self.db.query(PasswordResetCode).filter(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.consumed_at.is_(None),
        ).update({PasswordResetCode.consumed_at: now}, synchronize_session=False)
        self.db.query(AuthSession).filter(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        ).update({AuthSession.revoked_at: now}, synchronize_session=False)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)

    def _hash_password_reset_code(self, code: str, salt: str) -> str:
        """使用应用密钥与随机盐生成验证码摘要。"""

        message = f"{salt}:{code}".encode("utf-8")
        return hmac.new(
            self.settings.jwt_secret_key.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()
