"""记录 AI 首轮质检开始时间。"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_0017"
down_revision = "20260907_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_ai_annotation_reviews",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE extraction_ai_annotation_reviews "
        "SET started_at = created_at WHERE started_at IS NULL"
    )
    op.create_index(
        "ix_extraction_ai_annotation_reviews_started_at",
        "extraction_ai_annotation_reviews",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_ai_annotation_reviews_started_at",
        table_name="extraction_ai_annotation_reviews",
    )
    op.drop_column("extraction_ai_annotation_reviews", "started_at")
