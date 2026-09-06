"""统一 AI 标注生成与修复任务队列。"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_0014"
down_revision = "20260824_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_ai_annotation_jobs",
        sa.Column("operation", sa.String(length=32), nullable=False, server_default="generate"),
    )
    op.add_column(
        "extraction_ai_annotation_jobs",
        sa.Column("source_job_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "extraction_ai_annotation_jobs",
        sa.Column("input_content", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_operation",
        "extraction_ai_annotation_jobs",
        ["operation"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_source_job_id",
        "extraction_ai_annotation_jobs",
        ["source_job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_source_job_id",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_operation",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_column("extraction_ai_annotation_jobs", "input_content")
    op.drop_column("extraction_ai_annotation_jobs", "source_job_id")
    op.drop_column("extraction_ai_annotation_jobs", "operation")
