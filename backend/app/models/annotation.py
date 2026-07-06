"""同名人物标注与 AI 参考报告模型。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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


class IdentityAIReport(Base):
    """同名人物 AI 考据报告。

    source_person_id / target_person_id 按数值升序存储，保证同一人物对只有一份报告。
    """

    __tablename__ = "identity_ai_reports"
    __table_args__ = (
        UniqueConstraint(
            "source_person_id",
            "target_person_id",
            name="uq_identity_ai_reports_pair",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    target_person_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
