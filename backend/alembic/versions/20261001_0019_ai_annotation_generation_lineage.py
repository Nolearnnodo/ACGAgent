"""AI 标注工作副本按生成根分存，并允许归档生成。"""

from alembic import op
import sqlalchemy as sa


revision = "20261001_0019"
down_revision = "20260915_0018"
branch_labels = None
depends_on = None


def _lineage_job_id(bind, source_job_id: int | None) -> int | None:
    current_id = source_job_id
    seen: set[int] = set()
    while current_id is not None:
        if current_id in seen:
            return None
        seen.add(current_id)
        row = bind.execute(
            sa.text(
                "SELECT id, operation, source_job_id "
                "FROM extraction_ai_annotation_jobs WHERE id = :id"
            ),
            {"id": current_id},
        ).fetchone()
        if row is None:
            return None
        if row.operation == "generate":
            return int(row.id)
        current_id = row.source_job_id
    return None


def upgrade() -> None:
    op.add_column(
        "extraction_ai_annotation_jobs",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_archived_at",
        "extraction_ai_annotation_jobs",
        ["archived_at"],
    )
    op.add_column(
        "extraction_ai_annotation_saved_results",
        sa.Column("lineage_job_id", sa.Integer(), nullable=True),
    )

    bind = op.get_bind()
    saved_rows = bind.execute(
        sa.text("SELECT id, source_job_id FROM extraction_ai_annotation_saved_results")
    ).fetchall()
    for row in saved_rows:
        lineage_id = _lineage_job_id(bind, row.source_job_id)
        if lineage_id is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE extraction_ai_annotation_saved_results "
                "SET lineage_job_id = :lineage_id WHERE id = :id"
            ),
            {"lineage_id": lineage_id, "id": row.id},
        )

    with op.batch_alter_table("extraction_ai_annotation_saved_results") as batch_op:
        batch_op.drop_constraint(
            "uq_extraction_ai_annotation_saved_result_task_user",
            type_="unique",
        )
        batch_op.create_foreign_key(
            "fk_extraction_ai_annotation_saved_results_lineage_job_id",
            "extraction_ai_annotation_jobs",
            ["lineage_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_unique_constraint(
            "uq_extraction_ai_annotation_saved_result_lineage",
            ["task_id", "requested_by", "lineage_job_id"],
        )

    op.create_index(
        "ix_extraction_ai_annotation_saved_results_lineage_job_id",
        "extraction_ai_annotation_saved_results",
        ["lineage_job_id"],
    )
    op.create_index(
        "uq_extraction_ai_annotation_saved_result_null_lineage",
        "extraction_ai_annotation_saved_results",
        ["task_id", "requested_by"],
        unique=True,
        sqlite_where=sa.text("lineage_job_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_extraction_ai_annotation_saved_result_null_lineage",
        table_name="extraction_ai_annotation_saved_results",
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, task_id, requested_by FROM extraction_ai_annotation_saved_results "
            "ORDER BY updated_at DESC, id DESC"
        )
    ).fetchall()
    seen: set[tuple[int, int]] = set()
    for row in rows:
        key = (int(row.task_id), int(row.requested_by))
        if key in seen:
            bind.execute(
                sa.text("DELETE FROM extraction_ai_annotation_saved_results WHERE id = :id"),
                {"id": row.id},
            )
            continue
        seen.add(key)

    op.drop_index(
        "ix_extraction_ai_annotation_saved_results_lineage_job_id",
        table_name="extraction_ai_annotation_saved_results",
    )
    with op.batch_alter_table("extraction_ai_annotation_saved_results") as batch_op:
        batch_op.drop_constraint(
            "uq_extraction_ai_annotation_saved_result_lineage",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_extraction_ai_annotation_saved_results_lineage_job_id",
            type_="foreignkey",
        )
        batch_op.drop_column("lineage_job_id")
        batch_op.create_unique_constraint(
            "uq_extraction_ai_annotation_saved_result_task_user",
            ["task_id", "requested_by"],
        )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_archived_at",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_column("extraction_ai_annotation_jobs", "archived_at")
