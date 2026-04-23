"""Planner 与执行过程模型。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class PlannerDecisionRecord(Base):
    """Planner 输出的结构化决策记录。"""

    __tablename__ = "planner_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    intent: Mapped[str] = mapped_column(String(128), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_skill_code: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ExecutionRun(Base):
    """一次完整执行。"""

    __tablename__ = "execution_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    trigger_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    planner_decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("planner_decisions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    trigger_type: Mapped[str] = mapped_column(String(64), default="chat", nullable=False, index=True)
    passage_id: Mapped[int | None] = mapped_column(
        ForeignKey("passages.doc_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionStepRun(Base):
    """执行中的单步记录。"""

    __tablename__ = "execution_step_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_run_id: Mapped[int] = mapped_column(ForeignKey("execution_runs.id", ondelete="CASCADE"), index=True)
    step_no: Mapped[int] = mapped_column(Integer, nullable=False)
    skill_code: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    input_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    output_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
