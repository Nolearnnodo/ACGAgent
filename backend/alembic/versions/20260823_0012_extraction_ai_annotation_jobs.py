"""新增功能 A AI 标注持久化任务表。"""

from alembic import op
import sqlalchemy as sa


revision = "20260823_0012"
down_revision = "20260730_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_ai_annotation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("passage_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("prompt_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["extraction_annotation_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.doc_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_task_id",
        "extraction_ai_annotation_jobs",
        ["task_id"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_passage_id",
        "extraction_ai_annotation_jobs",
        ["passage_id"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_requested_by",
        "extraction_ai_annotation_jobs",
        ["requested_by"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_status",
        "extraction_ai_annotation_jobs",
        ["status"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_created_at",
        "extraction_ai_annotation_jobs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_created_at",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_status",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_requested_by",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_passage_id",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_task_id",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_table("extraction_ai_annotation_jobs")
