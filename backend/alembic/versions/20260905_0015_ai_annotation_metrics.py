"""记录 AI 标注耗时、Token、首轮错误与最终提交差异。"""

import json

from alembic import op
import sqlalchemy as sa


revision = "20260905_0015"
down_revision = "20260825_0014"
branch_labels = None
depends_on = None


def _safe_json(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def upgrade() -> None:
    job_columns = (
        sa.Column("client_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_cache_hit_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_cache_miss_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_metrics_supported", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("llm_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_round_validation_status", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("first_round_error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_round_errors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("first_round_warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_round_truncated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_difference_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_differences_json", sa.Text(), nullable=False, server_default="{}"),
    )
    for column in job_columns:
        op.add_column("extraction_ai_annotation_jobs", column)

    op.create_index(
        "ix_extraction_ai_annotation_jobs_client_started_at",
        "extraction_ai_annotation_jobs",
        ["client_started_at"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_submitted_at",
        "extraction_ai_annotation_jobs",
        ["submitted_at"],
    )
    op.create_index(
        "ix_extraction_ai_annotation_jobs_first_round_validation_status",
        "extraction_ai_annotation_jobs",
        ["first_round_validation_status"],
    )

    with op.batch_alter_table("extraction_annotation_submissions") as batch_op:
        batch_op.add_column(sa.Column("source_ai_job_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("ai_imported_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_extraction_annotation_submissions_source_ai_job_id",
            "extraction_ai_annotation_jobs",
            ["source_ai_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_extraction_annotation_submissions_source_ai_job_id",
            ["source_ai_job_id"],
        )

    # 旧任务没有浏览器点击时刻，使用服务端入队时刻作为可解释的回退值；
    # 已有 result_json 若包含新 usage 字段则一并回填，迁移可重复验证。
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, operation, status, error_message, result_json, created_at
            FROM extraction_ai_annotation_jobs
            """
        )
    ).mappings()
    for row in rows:
        result = _safe_json(row["result_json"])
        usage = result.get("token_usage") if isinstance(result.get("token_usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = max(
            int(usage.get("total_tokens") or 0),
            prompt_tokens + completion_tokens,
        )
        issues = result.get("validation_issues") if isinstance(result.get("validation_issues"), list) else []
        warnings = result.get("fragment_warnings") if isinstance(result.get("fragment_warnings"), list) else []
        truncated = result.get("truncated_fragments") if isinstance(result.get("truncated_fragments"), list) else []
        first_status = ""
        first_errors: list[dict] = []
        if row["operation"] == "generate":
            first_status = str(result.get("validation_status") or "")
            first_errors = [item for item in issues if isinstance(item, dict)]
            if row["status"] == "failed" and not first_status:
                first_status = "failed"
                first_errors = [
                    {
                        "path": "root",
                        "message": str(row["error_message"] or "历史 AI 标注任务失败"),
                        "code": "generation_failed",
                    }
                ]
        connection.execute(
            sa.text(
                """
                UPDATE extraction_ai_annotation_jobs
                SET client_started_at = :client_started_at,
                    prompt_tokens = :prompt_tokens,
                    completion_tokens = :completion_tokens,
                    total_tokens = :total_tokens,
                    prompt_cache_hit_tokens = :cache_hit_tokens,
                    prompt_cache_miss_tokens = :cache_miss_tokens,
                    cache_metrics_supported = :cache_supported,
                    llm_call_count = :call_count,
                    first_round_validation_status = :first_status,
                    first_round_error_count = :first_error_count,
                    first_round_errors_json = :first_errors_json,
                    first_round_warning_count = :warning_count,
                    first_round_truncated_count = :truncated_count
                WHERE id = :job_id
                """
            ),
            {
                "job_id": row["id"],
                "client_started_at": row["created_at"],
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
                "cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens") or 0),
                "cache_supported": bool(usage.get("cache_metrics_supported", False)),
                "call_count": int(usage.get("llm_call_count") or result.get("fragment_count") or 0),
                "first_status": first_status,
                "first_error_count": len(first_errors),
                "first_errors_json": json.dumps(first_errors, ensure_ascii=False),
                "warning_count": len(warnings) if row["operation"] == "generate" else 0,
                "truncated_count": len(truncated) if row["operation"] == "generate" else 0,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("extraction_annotation_submissions") as batch_op:
        batch_op.drop_index("ix_extraction_annotation_submissions_source_ai_job_id")
        batch_op.drop_constraint(
            "fk_extraction_annotation_submissions_source_ai_job_id",
            type_="foreignkey",
        )
        batch_op.drop_column("ai_imported_at")
        batch_op.drop_column("source_ai_job_id")

    op.drop_index(
        "ix_extraction_ai_annotation_jobs_first_round_validation_status",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_submitted_at",
        table_name="extraction_ai_annotation_jobs",
    )
    op.drop_index(
        "ix_extraction_ai_annotation_jobs_client_started_at",
        table_name="extraction_ai_annotation_jobs",
    )
    for column_name in (
        "final_differences_json",
        "final_difference_count",
        "first_round_truncated_count",
        "first_round_warning_count",
        "first_round_errors_json",
        "first_round_error_count",
        "first_round_validation_status",
        "llm_call_count",
        "cache_metrics_supported",
        "prompt_cache_miss_tokens",
        "prompt_cache_hit_tokens",
        "total_tokens",
        "completion_tokens",
        "prompt_tokens",
        "submitted_at",
        "client_started_at",
    ):
        op.drop_column("extraction_ai_annotation_jobs", column_name)
