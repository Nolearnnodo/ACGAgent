"""Seed DeepSeek V4 Flash pricing rules.

Revision ID: 20260524_0005
Revises: 20260524_0004
Create Date: 2026-05-24
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "20260524_0005"
down_revision = "20260524_0004"
branch_labels = None
depends_on = None


_PROVIDER = "deepseek"
_MODELS = ("deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner")


def upgrade() -> None:
    pricing_table = sa.table(
        "model_pricing_rules",
        sa.column("provider", sa.String),
        sa.column("model", sa.String),
        sa.column("currency", sa.String),
        sa.column("cache_hit_input_price_per_1m", sa.Float),
        sa.column("cache_miss_input_price_per_1m", sa.Float),
        sa.column("output_price_per_1m", sa.Float),
        sa.column("effective_from", sa.DateTime(timezone=True)),
        sa.column("effective_to", sa.DateTime(timezone=True)),
    )
    effective_from = datetime(2026, 4, 26, 12, 15, tzinfo=timezone.utc)
    op.bulk_insert(
        pricing_table,
        [
            {
                "provider": _PROVIDER,
                "model": model,
                "currency": "CNY",
                "cache_hit_input_price_per_1m": 0.02,
                "cache_miss_input_price_per_1m": 1.0,
                "output_price_per_1m": 2.0,
                "effective_from": effective_from,
                "effective_to": None,
            }
            for model in _MODELS
        ],
    )


def downgrade() -> None:
    model_list = ", ".join(f"'{model}'" for model in _MODELS)
    op.execute(
        f"""
        DELETE FROM model_pricing_rules
        WHERE provider = '{_PROVIDER}'
          AND model IN ({model_list})
          AND currency = 'CNY'
          AND cache_hit_input_price_per_1m = 0.02
          AND cache_miss_input_price_per_1m = 1.0
          AND output_price_per_1m = 2.0
        """
    )
