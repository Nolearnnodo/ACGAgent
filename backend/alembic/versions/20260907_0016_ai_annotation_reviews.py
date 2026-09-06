"""新增首轮 AI 标注人工语义质检记录。"""

from alembic import op
import sqlalchemy as sa


revision = "20260907_0016"
down_revision = "20260905_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_ai_annotation_reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "generation_job_id",
            sa.Integer(),
            sa.ForeignKey("extraction_ai_annotation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("extraction_annotation_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "passage_id",
            sa.Integer(),
            sa.ForeignKey("passages.doc_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("standard_version", sa.String(length=32), nullable=False),
        sa.Column("decisions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("omissions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("total_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviewed_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("incorrect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unreviewed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("omission_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accuracy_basis_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "generation_job_id",
            name="uq_extraction_ai_annotation_reviews_generation_job",
        ),
    )
    for column_name in (
        "generation_job_id",
        "task_id",
        "passage_id",
        "reviewer_id",
        "status",
        "submitted_at",
        "updated_at",
    ):
        op.create_index(
            f"ix_extraction_ai_annotation_reviews_{column_name}",
            "extraction_ai_annotation_reviews",
            [column_name],
        )


def downgrade() -> None:
    op.drop_table("extraction_ai_annotation_reviews")
