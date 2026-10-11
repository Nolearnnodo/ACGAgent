"""AI 标注任务增加 cloned_from_job_id，用于同步 canonical admin 结果。"""

from alembic import op
import sqlalchemy as sa


revision = "20260915_0018"
down_revision = "20260909_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_ai_annotation_jobs",
        sa.Column("cloned_from_job_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_cloned_from_job_id",
        "extraction_ai_annotation_jobs",
        ["cloned_from_job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_cloned_from_job_id",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_column("extraction_ai_annotation_jobs", "cloned_from_job_id")
