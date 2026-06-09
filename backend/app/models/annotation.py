"""同名人物标注模型。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class IdentityAnnotation(Base):
    __tablename__ = "identity_annotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    target_person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    human_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    annotator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
