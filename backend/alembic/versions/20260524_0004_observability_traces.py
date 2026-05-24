"""Add observability trace tables."""

import sqlalchemy as sa
from alembic import op

revision = "20260524_0004"
down_revision = "20260515_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_call_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_run_id", sa.Integer(), nullable=True),
        sa.Column("execution_step_run_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("passage_id", sa.Integer(), nullable=True),
        sa.Column("skill_code", sa.String(length=128), nullable=False),
        sa.Column("call_purpose", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_cache_hit_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_cache_miss_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_hit_ratio", sa.Float(), nullable=False),
        sa.Column("cache_metrics_supported", sa.Boolean(), nullable=False),
        sa.Column("estimated_input_cost", sa.Float(), nullable=False),
        sa.Column("estimated_output_cost", sa.Float(), nullable=False),
        sa.Column("estimated_total_cost", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_step_run_id"], ["execution_step_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.doc_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_llm_call_logs_execution_run_id", "llm_call_logs", ["execution_run_id"])
    op.create_index("ix_llm_call_logs_execution_step_run_id", "llm_call_logs", ["execution_step_run_id"])
    op.create_index("ix_llm_call_logs_conversation_id", "llm_call_logs", ["conversation_id"])
    op.create_index("ix_llm_call_logs_message_id", "llm_call_logs", ["message_id"])
    op.create_index("ix_llm_call_logs_passage_id", "llm_call_logs", ["passage_id"])
    op.create_index("ix_llm_call_logs_provider", "llm_call_logs", ["provider"])
    op.create_index("ix_llm_call_logs_model", "llm_call_logs", ["model"])

    op.create_table(
        "tool_call_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_run_id", sa.Integer(), nullable=True),
        sa.Column("execution_step_run_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("passage_id", sa.Integer(), nullable=True),
        sa.Column("skill_code", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_step_run_id"], ["execution_step_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.doc_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_tool_call_logs_execution_run_id", "tool_call_logs", ["execution_run_id"])
    op.create_index("ix_tool_call_logs_execution_step_run_id", "tool_call_logs", ["execution_step_run_id"])
    op.create_index("ix_tool_call_logs_conversation_id", "tool_call_logs", ["conversation_id"])
    op.create_index("ix_tool_call_logs_message_id", "tool_call_logs", ["message_id"])
    op.create_index("ix_tool_call_logs_passage_id", "tool_call_logs", ["passage_id"])

    op.create_table(
        "execution_trace_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_run_id", sa.Integer(), nullable=False),
        sa.Column("llm_call_count", sa.Integer(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_cache_hit_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_cache_miss_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_hit_ratio", sa.Float(), nullable=False),
        sa.Column("estimated_input_cost", sa.Float(), nullable=False),
        sa.Column("estimated_output_cost", sa.Float(), nullable=False),
        sa.Column("estimated_total_cost", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), nullable=False),
        sa.Column("failed_call_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("execution_run_id"),
    )
    op.create_index(
        "ix_execution_trace_summaries_execution_run_id",
        "execution_trace_summaries",
        ["execution_run_id"],
    )

    op.create_table(
        "model_pricing_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("cache_hit_input_price_per_1m", sa.Float(), nullable=False),
        sa.Column("cache_miss_input_price_per_1m", sa.Float(), nullable=False),
        sa.Column("output_price_per_1m", sa.Float(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_model_pricing_rules_provider", "model_pricing_rules", ["provider"])
    op.create_index("ix_model_pricing_rules_model", "model_pricing_rules", ["model"])


def downgrade() -> None:
    op.drop_index("ix_model_pricing_rules_model", table_name="model_pricing_rules")
    op.drop_index("ix_model_pricing_rules_provider", table_name="model_pricing_rules")
    op.drop_table("model_pricing_rules")

    op.drop_index(
        "ix_execution_trace_summaries_execution_run_id",
        table_name="execution_trace_summaries",
    )
    op.drop_table("execution_trace_summaries")

    op.drop_index("ix_tool_call_logs_passage_id", table_name="tool_call_logs")
    op.drop_index("ix_tool_call_logs_message_id", table_name="tool_call_logs")
    op.drop_index("ix_tool_call_logs_conversation_id", table_name="tool_call_logs")
    op.drop_index("ix_tool_call_logs_execution_step_run_id", table_name="tool_call_logs")
    op.drop_index("ix_tool_call_logs_execution_run_id", table_name="tool_call_logs")
    op.drop_table("tool_call_logs")

    op.drop_index("ix_llm_call_logs_model", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_provider", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_passage_id", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_message_id", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_conversation_id", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_execution_step_run_id", table_name="llm_call_logs")
    op.drop_index("ix_llm_call_logs_execution_run_id", table_name="llm_call_logs")
    op.drop_table("llm_call_logs")
