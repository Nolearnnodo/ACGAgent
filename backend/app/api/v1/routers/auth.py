"""鉴权接口。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenPair,
    UpdateProfileRequest,
    UserProfileResponse,
)
from app.schemas.common import MessageResponse
from app.services.auth_service import AuthService
from app.services.user_service import UserService

router = APIRouter(prefix="/auth", tags=["鉴权"])


@router.post("/register", response_model=AuthResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """用户注册。"""

    service = AuthService(db)
    user = service.register(email=payload.email, password=payload.password)
    access_token, refresh_token = service.create_token_pair(user)
    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        tokens=TokenPair(access_token=access_token, refresh_token=refresh_token),
    )


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """用户登录。"""

    service = AuthService(db)
    user = service.authenticate(email=payload.email, password=payload.password)
    access_token, refresh_token = service.create_token_pair(user)
    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        tokens=TokenPair(access_token=access_token, refresh_token=refresh_token),
    )


@router.post("/refresh", response_model=AuthResponse)
def refresh_token(payload: RefreshTokenRequest, db: Session = Depends(get_db)) -> AuthResponse:
    """刷新访问令牌。"""

    service = AuthService(db)
    user, access_token, refresh_token = service.refresh_tokens(payload.refresh_token)
    return AuthResponse(
        user=UserProfileResponse.model_validate(user),
        tokens=TokenPair(access_token=access_token, refresh_token=refresh_token),
    )


@router.post("/logout", response_model=MessageResponse)
def logout(payload: LogoutRequest, db: Session = Depends(get_db)) -> MessageResponse:
    """用户登出。"""

    service = AuthService(db)
    service.logout(payload.refresh_token)
    return MessageResponse(message="已成功登出。")


@router.get("/me", response_model=UserProfileResponse)
def get_me(current_user: User = Depends(get_current_user)) -> UserProfileResponse:
    """获取当前登录用户。"""

    return UserProfileResponse.model_validate(current_user)


@router.put("/me", response_model=UserProfileResponse)
def update_profile(
    payload: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    """更新当前用户资料。"""

    service = UserService(db)
    user = service.update_profile(current_user, email=payload.email, password=payload.password)
    return UserProfileResponse.model_validate(user)
