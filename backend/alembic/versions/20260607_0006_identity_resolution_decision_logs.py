"""新增同名人物逐轮裁定日志。"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0006"
down_revision = "20260524_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_resolution_decision_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_run_id", sa.Integer(), nullable=True),
        sa.Column("execution_step_run_id", sa.Integer(), nullable=True),
        sa.Column("passage_id", sa.Integer(), nullable=True),
        sa.Column("new_person_id", sa.Integer(), nullable=False),
        sa.Column("candidate_person_id", sa.Integer(), nullable=False),
        sa.Column("hop", sa.Integer(), nullable=False),
        sa.Column("focus_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("positive_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("negative_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("missing_evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("next_hop_focus_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("used_full_text", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["execution_run_id"],
            ["execution_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["execution_step_run_id"],
            ["execution_step_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["passage_id"],
            ["passages.doc_id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_identity_resolution_decision_logs_execution_run_id",
        "identity_resolution_decision_logs",
        ["execution_run_id"],
    )
    op.create_index(
        "ix_identity_resolution_decision_logs_execution_step_run_id",
        "identity_resolution_decision_logs",
        ["execution_step_run_id"],
    )
    op.create_index(
        "ix_identity_resolution_decision_logs_passage_id",
        "identity_resolution_decision_logs",
        ["passage_id"],
    )
    op.create_index(
        "ix_identity_resolution_decision_logs_new_person_id",
        "identity_resolution_decision_logs",
        ["new_person_id"],
    )
    op.create_index(
        "ix_identity_resolution_decision_logs_candidate_person_id",
        "identity_resolution_decision_logs",
        ["candidate_person_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_resolution_decision_logs_candidate_person_id",
        table_name="identity_resolution_decision_logs",
    )
    op.drop_index(
        "ix_identity_resolution_decision_logs_new_person_id",
        table_name="identity_resolution_decision_logs",
    )
    op.drop_index(
        "ix_identity_resolution_decision_logs_passage_id",
        table_name="identity_resolution_decision_logs",
    )
    op.drop_index(
        "ix_identity_resolution_decision_logs_execution_step_run_id",
        table_name="identity_resolution_decision_logs",
    )
    op.drop_index(
        "ix_identity_resolution_decision_logs_execution_run_id",
        table_name="identity_resolution_decision_logs",
    )
    op.drop_table("identity_resolution_decision_logs")
