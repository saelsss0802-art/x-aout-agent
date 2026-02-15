"""add search logs table

Revision ID: 0006_add_search_logs
Revises: 0005_add_cost_log_usage_fields
Create Date: 2026-02-15 09:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_add_search_logs"
down_revision: Union[str, Sequence[str], None] = "0005_add_cost_log_usage_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        default_json = sa.text("'[]'::jsonb")
    else:
        default_json = sa.text("'[]'")

    op.create_table(
        "search_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("results_json", JSONType, nullable=False, server_default=default_json),
        sa.Column("cost_estimate", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_search_logs_date", "search_logs", ["date"], unique=False)
    op.create_index("ix_search_logs_date_source", "search_logs", ["date", "source"], unique=False)
    op.create_index(op.f("ix_search_logs_agent_id"), "search_logs", ["agent_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_search_logs_agent_id"), table_name="search_logs")
    op.drop_index("ix_search_logs_date_source", table_name="search_logs")
    op.drop_index("ix_search_logs_date", table_name="search_logs")
    op.drop_table("search_logs")
