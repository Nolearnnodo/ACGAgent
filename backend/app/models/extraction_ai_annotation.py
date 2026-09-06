"""功能 A AI 标注后台任务模型。"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ExtractionAIAnnotationJob(Base):
    """一次 AI 标注请求的持久化队列记录。"""

    __tablename__ = "extraction_ai_annotation_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("extraction_annotation_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    passage_id: Mapped[int] = mapped_column(
        ForeignKey("passages.doc_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operation: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="generate",
        index=True,
    )
    source_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    input_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # API Key 刻意不写入数据库；任务重启后使用服务端默认 Key。
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prompt_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # client_started_at 由前端在用户点击“生成标注”时写入；created_at 仍保留
    # 服务端接收时刻，便于识别客户端时钟异常并计算排队时间。
    client_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_cache_hit_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_cache_miss_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_metrics_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    llm_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_round_validation_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
        index=True,
    )
    first_round_error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_round_errors_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    first_round_warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_round_truncated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_difference_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_differences_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ExtractionAIAnnotationSavedResult(Base):
    """每个用户在每篇任务上的当前 AI 结果，独立于前端页面生命周期。"""

    __tablename__ = "extraction_ai_annotation_saved_results"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "requested_by",
            name="uq_extraction_ai_annotation_saved_result_task_user",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("extraction_annotation_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    passage_id: Mapped[int] = mapped_column(
        ForeignKey("passages.doc_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("extraction_ai_annotation_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        index=True,
    )
