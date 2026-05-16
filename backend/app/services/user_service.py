"""用户服务。"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.user import User


class UserService:
    """封装用户资料相关逻辑。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_email(self, email: str) -> User | None:
        """按邮箱查询用户。"""

        return self.db.query(User).filter(User.email == email).first()

    def update_profile(self, user: User, email: str | None, password: str | None) -> User:
        """修改用户邮箱与密码。"""

        if email and email != user.email:
            existing = self.get_by_email(email)
            if existing is not None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该邮箱已被占用。")
            user.email = email

        if password:
            user.password_hash = get_password_hash(password)

        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
