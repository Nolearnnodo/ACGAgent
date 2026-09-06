"""为每篇 AI 标注任务保存用户当前结果。"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0013"
down_revision = "20260823_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_ai_annotation_saved_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("passage_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["extraction_annotation_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["passage_id"],
            ["passages.doc_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_job_id"],
            ["extraction_ai_annotation_jobs.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "task_id",
            "requested_by",
            name="uq_extraction_ai_annotation_saved_result_task_user",
        ),
    )
    op.create_index(
        "ix_extraction_ai_annotation_saved_results_task_id",
        "extraction_ai_annotation_saved_results",
        ["task_id"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_saved_results_passage_id",
        "extraction_ai_annotation_saved_results",
        ["passage_id"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_saved_results_requested_by",
        "extraction_ai_annotation_saved_results",
        ["requested_by"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_saved_results_source_job_id",
        "extraction_ai_annotation_saved_results",
        ["source_job_id"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_saved_results_created_at",
        "extraction_ai_annotation_saved_results",
        ["created_at"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_saved_results_updated_at",
        "extraction_ai_annotation_saved_results",
        ["updated_at"],
    )

    # 将迁移前已经完成的 AI 调用结果复制为当前结果，避免升级后旧篇目只能
    # 依赖旧任务记录而无法按篇目恢复。按创建时间升序写入，最后一条成功任务胜出。
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT task_id, passage_id, requested_by, id, result_json, created_at, updated_at
            FROM extraction_ai_annotation_jobs
            WHERE status = 'success' AND result_json IS NOT NULL
            ORDER BY created_at ASC, id ASC
            """
        )
    ).mappings()
    for row in rows:
        existing = connection.execute(
            sa.text(
                """
                SELECT id
                FROM extraction_ai_annotation_saved_results
                WHERE task_id = :task_id AND requested_by = :requested_by
                """
            ),
            {
                "task_id": row["task_id"],
                "requested_by": row["requested_by"],
            },
        ).first()
        values = {
            "task_id": row["task_id"],
            "passage_id": row["passage_id"],
            "requested_by": row["requested_by"],
            "source_job_id": row["id"],
            "result_json": row["result_json"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if existing is None:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO extraction_ai_annotation_saved_results
                    (task_id, passage_id, requested_by, source_job_id, result_json, created_at, updated_at)
                    VALUES
                    (:task_id, :passage_id, :requested_by, :source_job_id, :result_json, :created_at, :updated_at)
                    """
                ),
                values,
            )
        else:
            values["id"] = existing[0]
            connection.execute(
                sa.text(
                    """
                    UPDATE extraction_ai_annotation_saved_results
                    SET passage_id = :passage_id,
                        source_job_id = :source_job_id,
                        result_json = :result_json,
                        updated_at = :updated_at
                    WHERE id = :id
                    """
                ),
                values,
            )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_ai_annotation_saved_results_updated_at",
        table_name="extraction_ai_annotation_saved_results",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_saved_results_created_at",
        table_name="extraction_ai_annotation_saved_results",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_saved_results_source_job_id",
        table_name="extraction_ai_annotation_saved_results",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_saved_results_requested_by",
        table_name="extraction_ai_annotation_saved_results",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_saved_results_passage_id",
        table_name="extraction_ai_annotation_saved_results",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_saved_results_task_id",
        table_name="extraction_ai_annotation_saved_results",
    )
    op.drop_table("extraction_ai_annotation_saved_results")
