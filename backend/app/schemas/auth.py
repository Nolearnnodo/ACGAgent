"""鉴权相关数据模型。"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    """注册请求。"""

    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    """登录请求。"""

    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class UpdateProfileRequest(BaseModel):
    """修改个人信息请求。"""

    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=6, max_length=128)


class TokenPair(BaseModel):
    """访问令牌与刷新令牌。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserProfileResponse(BaseModel):
    """用户信息响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: str


class AuthResponse(BaseModel):
    """鉴权响应。"""

    user: UserProfileResponse
    tokens: TokenPair


class RefreshTokenRequest(BaseModel):
    """刷新令牌请求。"""

    refresh_token: str


class LogoutRequest(BaseModel):
    """登出请求。"""

    refresh_token: str
