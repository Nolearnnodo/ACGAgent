"""新增功能 A 裁定草稿与不可变金标版本表。"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_0010"
down_revision = "20260722_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_adjudication_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("resolutions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("gold_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("change_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["extraction_annotation_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("task_id", name="uq_extraction_adjudication_drafts_task"),
    )
    op.create_index(
        "ix_extraction_adjudication_drafts_task_id",
        "extraction_adjudication_drafts",
        ["task_id"],
    )
    op.create_index(
        "ix_extraction_adjudication_drafts_reviewer_id",
        "extraction_adjudication_drafts",
        ["reviewer_id"],
    )

    op.create_table(
        "extraction_gold_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("gold_json", sa.Text(), nullable=False),
        sa.Column("source_submission_ids_json", sa.Text(), nullable=False),
        sa.Column("adjudication_log_json", sa.Text(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["extraction_annotation_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "task_id",
            "version",
            name="uq_extraction_gold_versions_task_version",
        ),
    )
    op.create_index(
        "ix_extraction_gold_versions_task_id",
        "extraction_gold_versions",
        ["task_id"],
    )
    op.create_index(
        "ix_extraction_gold_versions_reviewer_id",
        "extraction_gold_versions",
        ["reviewer_id"],
    )
    op.create_index(
        "ix_extraction_gold_versions_locked_at",
        "extraction_gold_versions",
        ["locked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_gold_versions_locked_at", table_name="extraction_gold_versions")
    op.drop_index("ix_extraction_gold_versions_reviewer_id", table_name="extraction_gold_versions")
    op.drop_index("ix_extraction_gold_versions_task_id", table_name="extraction_gold_versions")
    op.drop_table("extraction_gold_versions")

    op.drop_index(
        "ix_extraction_adjudication_drafts_reviewer_id",
        table_name="extraction_adjudication_drafts",
    )
    op.drop_index(
        "ix_extraction_adjudication_drafts_task_id",
        table_name="extraction_adjudication_drafts",
    )
    op.drop_table("extraction_adjudication_drafts")
