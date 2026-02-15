"""add fetch logs table

Revision ID: 0007_add_fetch_logs
Revises: 0006_add_search_logs
Create Date: 2026-02-15 10:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_add_fetch_logs"
down_revision: Union[str, Sequence[str], None] = "0006_add_search_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "fetch_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("content_length", sa.Integer(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("summary_json", JSONType, nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("cost_estimate", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fetch_logs_date", "fetch_logs", ["date"], unique=False)
    op.create_index("ix_fetch_logs_date_status", "fetch_logs", ["date", "status"], unique=False)
    op.create_index(op.f("ix_fetch_logs_agent_id"), "fetch_logs", ["agent_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_fetch_logs_agent_id"), table_name="fetch_logs")
    op.drop_index("ix_fetch_logs_date_status", table_name="fetch_logs")
    op.drop_index("ix_fetch_logs_date", table_name="fetch_logs")
    op.drop_table("fetch_logs")
