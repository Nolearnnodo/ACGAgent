"""新增功能 A 知识抽取人工标注任务与提交表。"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_0009"
down_revision = "20260706_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_annotation_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("passage_id", sa.Integer(), nullable=False),
        sa.Column("context_sha256", sa.String(length=64), nullable=False),
        sa.Column("spec_version", sa.String(length=32), nullable=False, server_default="0.2.0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="open"),
        sa.Column("required_annotation_count", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.doc_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "passage_id",
            "context_sha256",
            "spec_version",
            name="uq_extraction_annotation_tasks_passage_version",
        ),
    )
    op.create_index(
        "ix_extraction_annotation_tasks_passage_id",
        "extraction_annotation_tasks",
        ["passage_id"],
    )
    op.create_index(
        "ix_extraction_annotation_tasks_context_sha256",
        "extraction_annotation_tasks",
        ["context_sha256"],
    )
    op.create_index(
        "ix_extraction_annotation_tasks_status",
        "extraction_annotation_tasks",
        ["status"],
    )
    op.create_index(
        "ix_extraction_annotation_tasks_priority",
        "extraction_annotation_tasks",
        ["priority"],
    )
    op.create_index(
        "ix_extraction_annotation_tasks_created_by",
        "extraction_annotation_tasks",
        ["created_by"],
    )

    op.create_table(
        "extraction_annotation_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("annotator_id", sa.Integer(), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("label_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["extraction_annotation_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["annotator_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "task_id",
            "annotator_id",
            name="uq_extraction_annotation_submissions_annotator",
        ),
        sa.UniqueConstraint(
            "task_id",
            "slot_no",
            name="uq_extraction_annotation_submissions_slot",
        ),
    )
    op.create_index(
        "ix_extraction_annotation_submissions_task_id",
        "extraction_annotation_submissions",
        ["task_id"],
    )
    op.create_index(
        "ix_extraction_annotation_submissions_annotator_id",
        "extraction_annotation_submissions",
        ["annotator_id"],
    )
    op.create_index(
        "ix_extraction_annotation_submissions_state",
        "extraction_annotation_submissions",
        ["state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_annotation_submissions_state",
        table_name="extraction_annotation_submissions",
    )
    op.drop_index(
        "ix_extraction_annotation_submissions_annotator_id",
        table_name="extraction_annotation_submissions",
    )
    op.drop_index(
        "ix_extraction_annotation_submissions_task_id",
        table_name="extraction_annotation_submissions",
    )
    op.drop_table("extraction_annotation_submissions")

    op.drop_index(
        "ix_extraction_annotation_tasks_created_by",
        table_name="extraction_annotation_tasks",
    )
    op.drop_index(
        "ix_extraction_annotation_tasks_priority",
        table_name="extraction_annotation_tasks",
    )
    op.drop_index(
        "ix_extraction_annotation_tasks_status",
        table_name="extraction_annotation_tasks",
    )
    op.drop_index(
        "ix_extraction_annotation_tasks_context_sha256",
        table_name="extraction_annotation_tasks",
    )
    op.drop_index(
        "ix_extraction_annotation_tasks_passage_id",
        table_name="extraction_annotation_tasks",
    )
    op.drop_table("extraction_annotation_tasks")
