"""初始完整数据库结构。"""

from alembic import op
import sqlalchemy as sa

revision = "20260423_0001"
down_revision = None
branch_labels = None
depends_on = None
def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_id", "users", ["id"], unique=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "skill_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("skill_type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("script_path", sa.String(length=255), nullable=False),
        sa.Column("input_schema_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("output_schema_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("allowed_roles_json", sa.Text(), nullable=False, server_default='["user"]'),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="1.0.0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="enabled"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_skill_definitions_code", "skill_definitions", ["code"], unique=True)

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False, server_default="新对话"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"], unique=False)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("refresh_jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)
    op.create_index("ix_auth_sessions_refresh_jti", "auth_sessions", ["refresh_jti"], unique=True)

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"], unique=False)

    op.create_table(
        "passages",
        sa.Column("doc_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("workflow_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_passages_title", "passages", ["title"], unique=False)
    op.create_index("ix_passages_created_by", "passages", ["created_by"], unique=False)

    op.create_table(
        "conversation_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("window_size", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("last_message_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_message_id"], ["messages.id"]),
        sa.UniqueConstraint("conversation_id"),
    )

    op.create_table(
        "planner_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("intent", sa.String(length=128), nullable=False),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("target_skill_code", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_planner_decisions_conversation_id", "planner_decisions", ["conversation_id"], unique=False)
    op.create_index("ix_planner_decisions_message_id", "planner_decisions", ["message_id"], unique=False)

    op.create_table(
        "execution_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("trigger_message_id", sa.Integer(), nullable=True),
        sa.Column("planner_decision_id", sa.Integer(), nullable=True),
        sa.Column("trigger_type", sa.String(length=64), nullable=False, server_default="chat"),
        sa.Column("passage_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trigger_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["planner_decision_id"], ["planner_decisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["passage_id"], ["passages.doc_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_execution_runs_conversation_id", "execution_runs", ["conversation_id"], unique=False)
    op.create_index("ix_execution_runs_trigger_message_id", "execution_runs", ["trigger_message_id"], unique=False)
    op.create_index("ix_execution_runs_planner_decision_id", "execution_runs", ["planner_decision_id"], unique=False)
    op.create_index("ix_execution_runs_trigger_type", "execution_runs", ["trigger_type"], unique=False)
    op.create_index("ix_execution_runs_passage_id", "execution_runs", ["passage_id"], unique=False)

    op.create_table(
        "execution_step_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("execution_run_id", sa.Integer(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False),
        sa.Column("skill_code", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("input_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("output_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_execution_step_runs_execution_run_id", "execution_step_runs", ["execution_run_id"], unique=False)

    op.create_table(
        "skill_relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_skill_id", sa.Integer(), nullable=False),
        sa.Column("child_skill_id", sa.Integer(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("step_alias", sa.String(length=64), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["parent_skill_id"], ["skill_definitions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["child_skill_id"], ["skill_definitions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_skill_relations_parent_skill_id", "skill_relations", ["parent_skill_id"], unique=False)
    op.create_index("ix_skill_relations_child_skill_id", "skill_relations", ["child_skill_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_skill_relations_child_skill_id", table_name="skill_relations")
    op.drop_index("ix_skill_relations_parent_skill_id", table_name="skill_relations")
    op.drop_table("skill_relations")

    op.drop_index("ix_execution_step_runs_execution_run_id", table_name="execution_step_runs")
    op.drop_table("execution_step_runs")

    op.drop_index("ix_execution_runs_passage_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_trigger_type", table_name="execution_runs")
    op.drop_index("ix_execution_runs_planner_decision_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_trigger_message_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_conversation_id", table_name="execution_runs")
    op.drop_table("execution_runs")

    op.drop_index("ix_planner_decisions_message_id", table_name="planner_decisions")
    op.drop_index("ix_planner_decisions_conversation_id", table_name="planner_decisions")
    op.drop_table("planner_decisions")

    op.drop_table("conversation_memories")

    op.drop_index("ix_passages_created_by", table_name="passages")
    op.drop_index("ix_passages_title", table_name="passages")
    op.drop_table("passages")

    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_auth_sessions_refresh_jti", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")

    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_skill_definitions_code", table_name="skill_definitions")
    op.drop_table("skill_definitions")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
