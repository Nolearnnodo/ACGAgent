"""新增同名人物 AI 考据报告表。"""

from alembic import op
import sqlalchemy as sa


revision = "20260706_0008"
down_revision = "20260609_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_ai_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_person_id", sa.Integer(), nullable=False),
        sa.Column("target_person_id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("target_name", sa.String(length=255), nullable=True),
        sa.Column("report_markdown", sa.Text(), nullable=False),
        sa.Column("generated_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generated_by_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "source_person_id",
            "target_person_id",
            name="uq_identity_ai_reports_pair",
        ),
    )
    op.create_index(
        "ix_identity_ai_reports_source_person_id",
        "identity_ai_reports",
        ["source_person_id"],
    )
    op.create_index(
        "ix_identity_ai_reports_target_person_id",
        "identity_ai_reports",
        ["target_person_id"],
    )
    op.create_index(
        "ix_identity_ai_reports_generated_by_id",
        "identity_ai_reports",
        ["generated_by_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_ai_reports_generated_by_id",
        table_name="identity_ai_reports",
    )
    op.drop_index(
        "ix_identity_ai_reports_target_person_id",
        table_name="identity_ai_reports",
    )
    op.drop_index(
        "ix_identity_ai_reports_source_person_id",
        table_name="identity_ai_reports",
    )
    op.drop_table("identity_ai_reports")
