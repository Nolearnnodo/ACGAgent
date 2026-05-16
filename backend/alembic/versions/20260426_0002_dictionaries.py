"""新增字典表 + passages 加列 + seed。

字典表四张：era_dictionary / historical_event_dictionary / source_type_dictionary
        / relation_code_dictionary。
passages 加 source_type_code（int, 0=墓志铭/塔铭, 1=一般历史文献）与 era 两列，
由 Skill-1 抽取后回写。
"""

import sqlalchemy as sa
from alembic import op

from app.db.seed.era_seed import ERA_SEED
from app.db.seed.historical_event_seed import HISTORICAL_EVENT_SEED
from app.db.seed.static_seed import RELATION_CODE_SEED, SOURCE_TYPE_SEED

revision = "20260426_0002"
down_revision = "20260423_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "era_dictionary",
        sa.Column("era", sa.String(length=64), primary_key=True),
        sa.Column("dynasty", sa.String(length=32), primary_key=True),
        sa.Column("start_year", sa.Integer(), nullable=False),
        sa.Column("end_year", sa.Integer(), nullable=False),
        sa.Column("simplified", sa.String(length=32), nullable=False),
        sa.Column("traditional", sa.String(length=32), nullable=False),
    )

    op.create_table(
        "historical_event_dictionary",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("year_label", sa.String(length=64), nullable=False),
        sa.Column("event_name", sa.String(length=128), nullable=False),
        sa.Column("event_details", sa.Text(), nullable=False, server_default=""),
        sa.Column("start_year", sa.Integer(), nullable=True),
        sa.Column("end_year", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_historical_event_dictionary_event_name",
        "historical_event_dictionary",
        ["event_name"],
        unique=True,
    )

    op.create_table(
        "source_type_dictionary",
        sa.Column("code", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )

    op.create_table(
        "relation_code_dictionary",
        sa.Column("code", sa.String(length=4), primary_key=True),
        sa.Column("meaning", sa.String(length=64), nullable=False),
        sa.Column("direction_hint", sa.Text(), nullable=False, server_default=""),
    )

    # passages 加列。SQLite ALTER TABLE 仅支持 ADD COLUMN，简单加非空默认列即可。
    with op.batch_alter_table("passages") as batch:
        batch.add_column(sa.Column("source_type_code", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("era", sa.String(length=64), nullable=True))

    op.create_index("ix_passages_source_type_code", "passages", ["source_type_code"], unique=False)
    op.create_index("ix_passages_era", "passages", ["era"], unique=False)

    # ===== seed =====
    era_table = sa.table(
        "era_dictionary",
        sa.column("era", sa.String),
        sa.column("dynasty", sa.String),
        sa.column("start_year", sa.Integer),
        sa.column("end_year", sa.Integer),
        sa.column("simplified", sa.String),
        sa.column("traditional", sa.String),
    )
    op.bulk_insert(
        era_table,
        [
            {
                "era": era,
                "dynasty": dynasty,
                "start_year": start_year,
                "end_year": end_year,
                "simplified": simplified,
                "traditional": traditional,
            }
            for era, dynasty, start_year, end_year, simplified, traditional in ERA_SEED
        ],
    )

    he_table = sa.table(
        "historical_event_dictionary",
        sa.column("year_label", sa.String),
        sa.column("event_name", sa.String),
        sa.column("event_details", sa.Text),
        sa.column("start_year", sa.Integer),
        sa.column("end_year", sa.Integer),
    )
    op.bulk_insert(
        he_table,
        [
            {
                "year_label": year_label,
                "event_name": event_name,
                "event_details": event_details,
                "start_year": start_year,
                "end_year": end_year,
            }
            for year_label, event_name, event_details, start_year, end_year in HISTORICAL_EVENT_SEED
        ],
    )

    st_table = sa.table(
        "source_type_dictionary",
        sa.column("code", sa.Integer),
        sa.column("label", sa.String),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        st_table,
        [
            {"code": code, "label": label, "description": description}
            for code, label, description in SOURCE_TYPE_SEED
        ],
    )

    rc_table = sa.table(
        "relation_code_dictionary",
        sa.column("code", sa.String),
        sa.column("meaning", sa.String),
        sa.column("direction_hint", sa.Text),
    )
    op.bulk_insert(
        rc_table,
        [
            {"code": code, "meaning": meaning, "direction_hint": direction_hint}
            for code, meaning, direction_hint in RELATION_CODE_SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_passages_era", table_name="passages")
    op.drop_index("ix_passages_source_type_code", table_name="passages")
    with op.batch_alter_table("passages") as batch:
        batch.drop_column("era")
        batch.drop_column("source_type_code")

    op.drop_table("relation_code_dictionary")
    op.drop_table("source_type_dictionary")
    op.drop_index(
        "ix_historical_event_dictionary_event_name",
        table_name="historical_event_dictionary",
    )
    op.drop_table("historical_event_dictionary")
    op.drop_table("era_dictionary")
