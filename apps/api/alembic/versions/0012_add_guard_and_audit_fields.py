"""add stop controls, post dedupe hash, and audit logs

Revision ID: 0012_add_guard_and_audit_fields
Revises: 0011_reconcile_x_usage_cost_fields
Create Date: 2026-02-19 10:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_add_guard_and_audit_fields"
down_revision: Union[str, Sequence[str], None] = "0011_reconcile_x_usage_cost_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("stop_reason", sa.String(length=255), nullable=True))
    op.add_column("agents", sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agents", sa.Column("stop_until", sa.DateTime(timezone=True), nullable=True))

    op.add_column("posts", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column("posts", sa.Column("content_bucket_date", sa.Date(), nullable=True))
    op.create_unique_constraint(
        "uq_posts_agent_content_dedupe",
        "posts",
        ["agent_id", "content_hash", "content_bucket_date"],
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_agent_date", "audit_logs", ["agent_id", "date"], unique=False)
    op.create_index("ix_audit_logs_source", "audit_logs", ["source"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_logs_source", table_name="audit_logs")
    op.drop_index("ix_audit_logs_agent_date", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_constraint("uq_posts_agent_content_dedupe", "posts", type_="unique")
    op.drop_column("posts", "content_bucket_date")
    op.drop_column("posts", "content_hash")

    op.drop_column("agents", "stop_until")
    op.drop_column("agents", "stopped_at")
    op.drop_column("agents", "stop_reason")
