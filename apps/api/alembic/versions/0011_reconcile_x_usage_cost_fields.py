"""add reconciled x usage cost fields

Revision ID: 0011_reconcile_x_usage_cost_fields
Revises: 0010_add_x_oauth_tokens
Create Date: 2026-02-19 00:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_reconcile_x_usage_cost_fields"
down_revision: Union[str, Sequence[str], None] = "0010_add_x_oauth_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cost_logs",
        sa.Column("x_api_cost_estimate", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "cost_logs",
        sa.Column("x_api_cost_actual", sa.Numeric(12, 2), nullable=True),
    )

    op.execute("UPDATE cost_logs SET x_api_cost_estimate = x_api_cost")

    with op.batch_alter_table("cost_logs") as batch_op:
        batch_op.alter_column("x_api_cost_estimate", server_default=None)
        batch_op.alter_column("x_usage_units", nullable=True, server_default=None)
        batch_op.alter_column("x_usage_raw", nullable=True, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("cost_logs") as batch_op:
        batch_op.alter_column("x_usage_raw", nullable=False)
        batch_op.alter_column("x_usage_units", nullable=False)

    op.drop_column("cost_logs", "x_api_cost_actual")
    op.drop_column("cost_logs", "x_api_cost_estimate")
