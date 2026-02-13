"""add posts external_id

Revision ID: 0004_add_posts_external_id
Revises: 0003_enforce_metrics_uniqueness_and_enum_alignment
Create Date: 2026-02-12 00:50:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_add_posts_external_id"
down_revision: Union[str, Sequence[str], None] = "0003_enforce_metrics_uniqueness_and_enum_alignment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.create_unique_constraint("uq_posts_agent_external_id", "posts", ["agent_id", "external_id"])


def downgrade() -> None:
    op.drop_constraint("uq_posts_agent_external_id", "posts", type_="unique")
    op.drop_column("posts", "external_id")
