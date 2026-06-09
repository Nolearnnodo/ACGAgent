"""新增同名人物标注表。"""

from alembic import op
import sqlalchemy as sa


revision = "20260609_0007"
down_revision = "20260607_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_annotations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_person_id", sa.Integer(), nullable=False, index=True),
        sa.Column("target_person_id", sa.Integer(), nullable=False, index=True),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("target_name", sa.String(length=255), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("human_confidence", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "annotator_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("identity_annotations")
