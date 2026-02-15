"""add usage fields to cost logs

Revision ID: 0005_add_cost_log_usage_fields
Revises: 0004_add_posts_external_id
Create Date: 2026-02-13 10:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005_add_cost_log_usage_fields"
down_revision: Union[str, Sequence[str], None] = "0004_add_posts_external_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        default_json = sa.text("'{}'::jsonb")
    else:
        default_json = sa.text("'{}'")

    op.add_column("cost_logs", sa.Column("x_usage_units", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "cost_logs",
        sa.Column(
            "x_usage_raw",
            JSONType,
            nullable=False,
            server_default=default_json,
        ),
    )


def downgrade() -> None:
    op.drop_column("cost_logs", "x_usage_raw")
    op.drop_column("cost_logs", "x_usage_units")
