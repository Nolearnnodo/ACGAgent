"""用户接口。"""

from fastapi import APIRouter, Depends

from app.core.deps import get_current_admin_user
from app.models.user import User
from app.schemas.auth import UserProfileResponse

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("/admin/me", response_model=UserProfileResponse)
def get_admin_profile(current_admin: User = Depends(get_current_admin_user)) -> UserProfileResponse:
    """示例管理员接口，用于验证管理员鉴权链路。"""

    return UserProfileResponse.model_validate(current_admin)
