"""enforce metrics uniqueness and enum alignment

Revision ID: 0003_enforce_metrics_uniqueness_and_enum_alignment
Revises: 0002_add_core_tables
Create Date: 2026-02-12 00:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_enforce_metrics_uniqueness_and_enum_alignment"
down_revision: Union[str, Sequence[str], None] = "0002_add_core_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_POST_TYPE = "post_type"
NEW_POST_TYPE = "post_type_enum"
OLD_METRICS_COLLECTION = "metrics_collection_type"
NEW_METRICS_COLLECTION = "metrics_collection_type_enum"
OLD_METRICS_SCHEDULE = "metrics_schedule_type"
NEW_METRICS_SCHEDULE = "metrics_schedule_type_enum"


def upgrade() -> None:
    op.execute("ALTER TYPE post_type RENAME TO post_type_old")
    op.execute("CREATE TYPE post_type_enum AS ENUM ('tweet', 'thread', 'quote_rt', 'poll')")
    op.execute(
        """
        ALTER TABLE posts
        ALTER COLUMN type TYPE post_type_enum
        USING (
            CASE type::text
                WHEN 'post' THEN 'tweet'
                WHEN 'reply' THEN 'thread'
                WHEN 'quote' THEN 'quote_rt'
                ELSE type::text
            END
        )::post_type_enum
        """
    )
    op.execute("DROP TYPE post_type_old")

    op.execute(f"ALTER TYPE {OLD_METRICS_COLLECTION} RENAME TO {OLD_METRICS_COLLECTION}_old")
    op.execute("CREATE TYPE metrics_collection_type_enum AS ENUM ('snapshot', 'confirmed')")
    op.execute(
        """
        ALTER TABLE post_metrics
        ALTER COLUMN collection_type TYPE metrics_collection_type_enum
        USING collection_type::text::metrics_collection_type_enum
        """
    )
    op.execute(f"DROP TYPE {OLD_METRICS_COLLECTION}_old")

    op.execute(f"ALTER TYPE {OLD_METRICS_SCHEDULE} RENAME TO {OLD_METRICS_SCHEDULE}_old")
    op.execute("CREATE TYPE metrics_schedule_type_enum AS ENUM ('snapshot', 'confirmed')")
    op.execute(
        """
        ALTER TABLE metrics_schedules
        ALTER COLUMN type TYPE metrics_schedule_type_enum
        USING type::text::metrics_schedule_type_enum
        """
    )
    op.execute(f"DROP TYPE {OLD_METRICS_SCHEDULE}_old")

    op.create_index(
        "ix_postmetrics_post_type_time",
        "post_metrics",
        ["post_id", "collection_type", "collected_at"],
    )
    op.create_unique_constraint(
        "uq_postmetrics_post_type_time",
        "post_metrics",
        ["post_id", "collection_type", "collected_at"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_postmetrics_post_type_time", "post_metrics", type_="unique")
    op.drop_index("ix_postmetrics_post_type_time", table_name="post_metrics")

    op.execute("ALTER TYPE metrics_schedule_type_enum RENAME TO metrics_schedule_type_enum_old")
    op.execute("CREATE TYPE metrics_schedule_type AS ENUM ('snapshot', 'confirmed')")
    op.execute(
        """
        ALTER TABLE metrics_schedules
        ALTER COLUMN type TYPE metrics_schedule_type
        USING type::text::metrics_schedule_type
        """
    )
    op.execute("DROP TYPE metrics_schedule_type_enum_old")

    op.execute("ALTER TYPE metrics_collection_type_enum RENAME TO metrics_collection_type_enum_old")
    op.execute("CREATE TYPE metrics_collection_type AS ENUM ('snapshot', 'confirmed')")
    op.execute(
        """
        ALTER TABLE post_metrics
        ALTER COLUMN collection_type TYPE metrics_collection_type
        USING collection_type::text::metrics_collection_type
        """
    )
    op.execute("DROP TYPE metrics_collection_type_enum_old")

    op.execute("ALTER TYPE post_type_enum RENAME TO post_type_enum_old")
    op.execute("CREATE TYPE post_type AS ENUM ('post', 'reply', 'quote')")
    op.execute(
        """
        ALTER TABLE posts
        ALTER COLUMN type TYPE post_type
        USING (
            CASE type::text
                WHEN 'tweet' THEN 'post'
                WHEN 'thread' THEN 'reply'
                WHEN 'quote_rt' THEN 'quote'
                WHEN 'poll' THEN 'post'
                ELSE type::text
            END
        )::post_type
        """
    )
    op.execute("DROP TYPE post_type_enum_old")
