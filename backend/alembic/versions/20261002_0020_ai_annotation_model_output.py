"""保存 AI 标注模型原文，供校验失败后人工补回。"""

from alembic import op
import sqlalchemy as sa


revision = "20261002_0020"
down_revision = "20261001_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_ai_annotation_jobs",
        sa.Column("model_output_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_ai_annotation_jobs", "model_output_json")
