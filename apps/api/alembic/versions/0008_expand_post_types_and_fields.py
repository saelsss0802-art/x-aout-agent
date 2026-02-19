"""expand post types and fields

Revision ID: 0008_expand_post_types_and_fields
Revises: 0007_add_fetch_logs
Create Date: 2026-02-15 11:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0008_expand_post_types_and_fields"
down_revision: Union[str, Sequence[str], None] = "0007_add_fetch_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE post_type_enum ADD VALUE IF NOT EXISTS 'reply'")

    op.add_column("posts", sa.Column("target_post_url", sa.String(length=2048), nullable=True))
    op.add_column("posts", sa.Column("thread_parts_json", JSONType, nullable=True))
    op.add_column("posts", sa.Column("allow_url", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("posts", "allow_url")
    op.drop_column("posts", "thread_parts_json")
    op.drop_column("posts", "target_post_url")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE post_type_enum RENAME TO post_type_enum_old")
        op.execute("CREATE TYPE post_type_enum AS ENUM ('tweet', 'thread', 'quote_rt', 'poll')")
        op.execute(
            "ALTER TABLE posts ALTER COLUMN type TYPE post_type_enum USING type::text::post_type_enum"
        )
        op.execute("DROP TYPE post_type_enum_old")
