"""add target post candidates

Revision ID: 0009_add_target_post_candidates
Revises: 0008_expand_post_types_and_fields
Create Date: 2026-02-19 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_add_target_post_candidates"
down_revision: Union[str, Sequence[str], None] = "0008_expand_post_types_and_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "target_post_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("target_handle", sa.String(length=255), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("post_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at_ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_target_post_candidates_agent_date", "target_post_candidates", ["agent_id", "date"])
    op.create_index(
        "ix_target_post_candidates_agent_date_used",
        "target_post_candidates",
        ["agent_id", "date", "used"],
    )


def downgrade() -> None:
    op.drop_index("ix_target_post_candidates_agent_date_used", table_name="target_post_candidates")
    op.drop_index("ix_target_post_candidates_agent_date", table_name="target_post_candidates")
    op.drop_table("target_post_candidates")
