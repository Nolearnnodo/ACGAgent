"""Add passage content hash for duplicate upload skipping."""

import sqlalchemy as sa
from alembic import op

revision = "20260515_0003"
down_revision = "20260426_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("passages") as batch:
        batch.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))

    op.create_index("ix_passages_content_hash", "passages", ["content_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_passages_content_hash", table_name="passages")

    with op.batch_alter_table("passages") as batch:
        batch.drop_column("content_hash")
