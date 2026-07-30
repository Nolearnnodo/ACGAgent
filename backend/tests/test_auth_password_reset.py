from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.routers import auth as auth_router
from app.core.config import Settings
from app.core.security import get_password_hash, verify_password
from app.db.base import Base
from app.db.session import get_db
from app.models.auth import AuthSession, PasswordResetCode
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.email_service import (
    EmailConfigurationError,
    EmailDeliveryError,
    EmailService,
)


class FakeEmailService:
    def __init__(
        self,
        *,
        configuration_error: bool = False,
        delivery_error: bool = False,
    ) -> None:
        self.configuration_error = configuration_error
        self.delivery_error = delivery_error
        self.sent: list[tuple[str, str]] = []

    def ensure_configured(self) -> None:
        if self.configuration_error:
            raise EmailConfigurationError("test configuration error")

    def send_password_reset_code(self, recipient: str, code: str) -> None:
        if self.delivery_error:
            raise EmailDeliveryError("test delivery error")
        self.sent.append((recipient, code))


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def reset_settings():
    return Settings(
        _env_file=None,
        JWT_SECRET_KEY="password-reset-test-secret",
        JWT_REFRESH_TOKEN_EXPIRE_DAYS=7,
        SMTP_HOST="smtp.example.com",
        SMTP_FROM_EMAIL="no-reply@example.com",
        PASSWORD_RESET_CODE_EXPIRE_MINUTES=10,
        PASSWORD_RESET_CODE_COOLDOWN_SECONDS=60,
        PASSWORD_RESET_MAX_ATTEMPTS=5,
    )


@pytest.fixture
def user(db):
    item = User(
        email="reader@example.com",
        password_hash=get_password_hash("old-password"),
        role="user",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def make_service(db, reset_settings, email_service):
    return AuthService(
        db,
        settings=reset_settings,
        email_service=email_service,
    )


def test_request_password_reset_sends_code_and_only_persists_hash(db, user, reset_settings):
    email_service = FakeEmailService()
    service = make_service(db, reset_settings, email_service)

    message = service.request_password_reset(user.email)

    assert message == AuthService.password_reset_request_message
    assert len(email_service.sent) == 1
    recipient, plain_code = email_service.sent[0]
    assert recipient == user.email
    assert len(plain_code) == 6
    assert plain_code.isdigit()
    assert plain_code[0] != "0"

    stored = db.query(PasswordResetCode).one()
    assert stored.code_hash != plain_code
    assert plain_code not in stored.code_hash
    assert len(stored.code_salt) == 32
    assert stored.consumed_at is None


def test_request_password_reset_hides_unknown_email_and_does_not_send(db, reset_settings):
    email_service = FakeEmailService()
    service = make_service(db, reset_settings, email_service)

    message = service.request_password_reset("missing@example.com")

    assert message == AuthService.password_reset_request_message
    assert email_service.sent == []
    assert db.query(PasswordResetCode).count() == 0


def test_request_password_reset_respects_send_cooldown(db, user, reset_settings):
    email_service = FakeEmailService()
    service = make_service(db, reset_settings, email_service)

    service.request_password_reset(user.email)
    service.request_password_reset(user.email)

    assert len(email_service.sent) == 1
    assert db.query(PasswordResetCode).count() == 1


def test_reset_password_consumes_code_and_revokes_refresh_sessions(db, user, reset_settings):
    email_service = FakeEmailService()
    service = make_service(db, reset_settings, email_service)
    service.request_password_reset(user.email)
    code = email_service.sent[0][1]

    auth_session = AuthSession(
        user_id=user.id,
        refresh_jti="active-session",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(auth_session)
    db.commit()

    service.reset_password(user.email, code, "new-password")

    db.expire_all()
    updated_user = db.get(User, user.id)
    stored_code = db.query(PasswordResetCode).one()
    stored_session = db.query(AuthSession).one()
    assert verify_password("new-password", updated_user.password_hash)
    assert stored_code.consumed_at is not None
    assert stored_session.revoked_at is not None

    with pytest.raises(HTTPException) as error:
        service.reset_password(user.email, code, "another-password")
    assert error.value.status_code == 400
    assert error.value.detail == AuthService.invalid_reset_code_message


def test_wrong_code_is_limited_to_max_attempts(db, user, reset_settings):
    email_service = FakeEmailService()
    service = make_service(db, reset_settings, email_service)
    service.request_password_reset(user.email)

    for attempt in range(reset_settings.password_reset_max_attempts):
        with pytest.raises(HTTPException):
            service.reset_password(user.email, "999999", "new-password")
        db.expire_all()
        stored = db.query(PasswordResetCode).one()
        assert stored.failed_attempts == attempt + 1

    assert stored.consumed_at is not None


def test_expired_code_cannot_reset_password(db, user, reset_settings):
    email_service = FakeEmailService()
    service = make_service(db, reset_settings, email_service)
    service.request_password_reset(user.email)
    code = email_service.sent[0][1]

    stored = db.query(PasswordResetCode).one()
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(HTTPException) as error:
        service.reset_password(user.email, code, "new-password")
    assert error.value.status_code == 400
    assert verify_password("old-password", db.get(User, user.id).password_hash)


def test_email_configuration_is_checked_before_user_lookup(db, reset_settings):
    service = make_service(
        db,
        reset_settings,
        FakeEmailService(configuration_error=True),
    )

    with pytest.raises(HTTPException) as error:
        service.request_password_reset("missing@example.com")

    assert error.value.status_code == 503
    assert error.value.detail == "邮件服务暂不可用，请稍后再试。"


def test_delivery_failure_keeps_generic_response_and_rolls_back_code(db, user, reset_settings):
    service = make_service(
        db,
        reset_settings,
        FakeEmailService(delivery_error=True),
    )

    message = service.request_password_reset(user.email)

    assert message == AuthService.password_reset_request_message
    assert db.query(PasswordResetCode).count() == 0


def test_password_reset_api_flow_can_log_in_with_new_password(monkeypatch, reset_settings):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    db = testing_session()
    db.add(
        User(
            email="api-reader@example.com",
            password_hash=get_password_hash("old-password"),
            role="user",
        )
    )
    db.commit()

    email_service = FakeEmailService()
    monkeypatch.setattr("app.services.auth_service.get_settings", lambda: reset_settings)
    monkeypatch.setattr("app.services.auth_service.EmailService", lambda _settings: email_service)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1")

    def override_get_db():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        send_response = client.post(
            "/api/v1/auth/password-reset/code",
            json={"email": "api-reader@example.com"},
        )
        assert send_response.status_code == 200
        assert send_response.json() == {
            "message": AuthService.password_reset_request_message,
            "cooldown_seconds": 60,
        }

        code = email_service.sent[0][1]
        reset_response = client.post(
            "/api/v1/auth/password-reset",
            json={
                "email": "api-reader@example.com",
                "code": code,
                "new_password": "new-password",
            },
        )
        assert reset_response.status_code == 200

        login_response = client.post(
            "/api/v1/auth/login",
            json={"email": "api-reader@example.com", "password": "new-password"},
        )
        assert login_response.status_code == 200
        assert login_response.json()["user"]["email"] == "api-reader@example.com"

    db.close()


def test_email_service_sends_utf8_message_over_starttls(monkeypatch):
    calls: list[object] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, *, context):
            calls.append(("starttls", context is not None))

        def login(self, username, password):
            calls.append(("login", username, password))

        def send_message(self, message):
            calls.append(("message", message))

    settings = Settings(
        _env_file=None,
        SMTP_HOST="smtp.example.com",
        SMTP_PORT=587,
        SMTP_USERNAME="mailer@example.com",
        SMTP_PASSWORD="authorization-code",
        SMTP_FROM_NAME="ACGAgent 测试",
        SMTP_USE_TLS=True,
        SMTP_USE_SSL=False,
        SMTP_TIMEOUT_SECONDS=8,
    )
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", FakeSMTP)

    EmailService(settings).send_password_reset_code("reader@example.com", "123456")

    assert calls[0] == ("connect", "smtp.example.com", 587, 8)
    assert ("starttls", True) in calls
    assert ("login", "mailer@example.com", "authorization-code") in calls
    message = next(call[1] for call in calls if isinstance(call, tuple) and call[0] == "message")
    assert message["To"] == "reader@example.com"
    assert "密码重置验证码" in message["Subject"]
    assert "123456" in message.get_body(preferencelist=("plain",)).get_content()
