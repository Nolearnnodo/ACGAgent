"""功能 A 知识抽取人工标注模型。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ExtractionAnnotationTask(Base):
    """一篇古籍的独立盲标任务。"""

    __tablename__ = "extraction_annotation_tasks"
    __table_args__ = (
        UniqueConstraint(
            "passage_id",
            "context_sha256",
            "spec_version",
            name="uq_extraction_annotation_tasks_passage_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    passage_id: Mapped[int] = mapped_column(
        ForeignKey("passages.doc_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    spec_version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.2.0")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open", index=True)
    required_annotation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExtractionAnnotationSubmission(Base):
    """标注员在一个盲标槽位中的版本化提交。"""

    __tablename__ = "extraction_annotation_submissions"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "annotator_id",
            name="uq_extraction_annotation_submissions_annotator",
        ),
        UniqueConstraint(
            "task_id",
            "slot_no",
            name="uq_extraction_annotation_submissions_slot",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("extraction_annotation_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    annotator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    label_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExtractionAdjudicationDraft(Base):
    """复核员尚未锁定的裁定草稿。"""

    __tablename__ = "extraction_adjudication_drafts"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_extraction_adjudication_drafts_task"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("extraction_annotation_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resolutions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    gold_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    change_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExtractionGoldVersion(Base):
    """裁定后锁定的不可变人工金标版本。"""

    __tablename__ = "extraction_gold_versions"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "version",
            name="uq_extraction_gold_versions_task_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("extraction_annotation_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    gold_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_submission_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    adjudication_log_json: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
