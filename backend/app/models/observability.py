"""Observability models for LLM, tool, and cost tracing."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    execution_step_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_step_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    passage_id: Mapped[int | None] = mapped_column(
        ForeignKey("passages.doc_id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    skill_code: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    call_purpose: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    request_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    response_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    response_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_cache_miss_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cache_metrics_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    estimated_input_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_output_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_total_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="CNY", nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ToolCallLog(Base):
    __tablename__ = "tool_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    execution_step_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_step_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    passage_id: Mapped[int | None] = mapped_column(
        ForeignKey("passages.doc_id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    skill_code: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    input_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    output_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ExecutionTraceSummary(Base):
    __tablename__ = "execution_trace_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_run_id: Mapped[int] = mapped_column(
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    llm_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_cache_hit_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_cache_miss_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_hit_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_input_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_output_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    estimated_total_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="CNY", nullable=False)
    total_latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class ModelPricingRule(Base):
    __tablename__ = "model_pricing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    model: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="CNY", nullable=False)
    cache_hit_input_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cache_miss_input_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    output_price_per_1m: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
