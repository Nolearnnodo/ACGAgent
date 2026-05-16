"""古籍文章模型。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Passage(Base):
    """古籍文章实体。

    对应需求中的 Passage_Info，作为上传与手工录入后的统一持久化对象。
    """

    __tablename__ = "passages"

    doc_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # source_type_code 与 era 由 Skill-1 抽取后回写：
    # source_type_code 0=墓志铭/塔铭(一手资料), 1=一般历史文献(列传/方志等)。
    # source_type 字段表示输入方式 (upload/manual_input)，与上面的语义不同，二者并存。
    source_type_code: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    era: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
