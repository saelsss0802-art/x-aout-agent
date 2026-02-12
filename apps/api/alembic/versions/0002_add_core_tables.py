"""add core tables

Revision ID: 0002_add_core_tables
Revises: 0001_init
Create Date: 2026-02-12 00:10:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_add_core_tables"
down_revision: Union[str, Sequence[str], None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


account_type = sa.Enum("individual", "business", name="account_type")
agent_status = sa.Enum("active", "paused", "disabled", name="agent_status")
post_type = sa.Enum("post", "reply", "quote", name="post_type")
metrics_collection_type = sa.Enum("snapshot", "confirmed", name="metrics_collection_type")
metrics_schedule_type = sa.Enum("snapshot", "confirmed", name="metrics_schedule_type")
experiment_status = sa.Enum("draft", "running", "completed", "cancelled", name="experiment_status")
action_type = sa.Enum("like", "reply", "quote_rt", name="action_type")


def upgrade() -> None:
    bind = op.get_bind()
    account_type.create(bind, checkfirst=True)
    agent_status.create(bind, checkfirst=True)
    post_type.create(bind, checkfirst=True)
    metrics_collection_type.create(bind, checkfirst=True)
    metrics_schedule_type.create(bind, checkfirst=True)
    experiment_status.create(bind, checkfirst=True)
    action_type.create(bind, checkfirst=True)

    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", account_type, nullable=False),
        sa.Column("api_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("media_assets_path", sa.String(length=1024), nullable=False),
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("status", agent_status, nullable=False),
        sa.Column("feature_toggles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("daily_budget", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("budget_split_x", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("budget_split_llm", sa.Integer(), nullable=False, server_default="200"),
    )
    op.create_index("ix_agents_account_id", "agents", ["account_id"])

    op.create_table(
        "account_knowledges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("tone", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("ng_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reference_accounts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_account_knowledges_account_id", "account_knowledges", ["account_id"])

    op.create_table(
        "experiments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("primary_metric", sa.String(length=255), nullable=False),
        sa.Column("variants", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision_rule", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", experiment_status, nullable=False),
    )
    op.create_index("ix_experiments_agent_id", "experiments", ["agent_id"])

    op.create_table(
        "target_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("handle", sa.String(length=255), nullable=False),
        sa.Column("like_limit", sa.Integer(), nullable=False),
        sa.Column("reply_limit", sa.Integer(), nullable=False),
        sa.Column("quote_rt_limit", sa.Integer(), nullable=False),
    )
    op.create_index("ix_target_accounts_agent_id", "target_accounts", ["agent_id"])

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("type", post_type, nullable=False),
        sa.Column("media_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("experiment_id", sa.Integer(), sa.ForeignKey("experiments.id"), nullable=True),
        sa.Column("variant", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_posts_agent_id", "posts", ["agent_id"])
    op.create_index("ix_posts_posted_at", "posts", ["posted_at"])

    op.create_table(
        "post_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("engagements", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retweets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replies", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("collection_type", metrics_collection_type, nullable=False),
    )
    op.create_index("ix_post_metrics_post_id", "post_metrics", ["post_id"])
    op.create_index("ix_post_metrics_collected_at", "post_metrics", ["collected_at"])

    op.create_table(
        "metrics_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("collect_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", metrics_schedule_type, nullable=False),
    )
    op.create_index("ix_metrics_schedules_agent_id", "metrics_schedules", ["agent_id"])

    op.create_table(
        "daily_pdca",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("analytics_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("strategy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("posts_created", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_daily_pdca_agent_id", "daily_pdca", ["agent_id"])
    op.create_index("ix_daily_pdca_date", "daily_pdca", ["date"])

    op.create_table(
        "local_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("insight", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_local_memories_agent_id", "local_memories", ["agent_id"])

    op.create_table(
        "shared_knowledge",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("results", sa.Text(), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("trust_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("contributed_by", sa.String(length=255), nullable=False),
        sa.Column("evidence_type", sa.String(length=128), nullable=False),
    )

    op.create_table(
        "engagement_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("target_account_id", sa.Integer(), sa.ForeignKey("target_accounts.id"), nullable=False),
        sa.Column("action_type", action_type, nullable=False),
        sa.Column("target_post_url", sa.String(length=1024), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_engagement_actions_agent_id", "engagement_actions", ["agent_id"])

    op.create_table(
        "cost_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("x_api_cost", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("llm_cost", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("image_gen_cost", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
    )
    op.create_index("ix_cost_logs_agent_id", "cost_logs", ["agent_id"])
    op.create_index("ix_cost_logs_date", "cost_logs", ["date"])


def downgrade() -> None:
    op.drop_index("ix_cost_logs_date", table_name="cost_logs")
    op.drop_index("ix_cost_logs_agent_id", table_name="cost_logs")
    op.drop_table("cost_logs")

    op.drop_index("ix_engagement_actions_agent_id", table_name="engagement_actions")
    op.drop_table("engagement_actions")

    op.drop_table("shared_knowledge")

    op.drop_index("ix_local_memories_agent_id", table_name="local_memories")
    op.drop_table("local_memories")

    op.drop_index("ix_daily_pdca_date", table_name="daily_pdca")
    op.drop_index("ix_daily_pdca_agent_id", table_name="daily_pdca")
    op.drop_table("daily_pdca")

    op.drop_index("ix_metrics_schedules_agent_id", table_name="metrics_schedules")
    op.drop_table("metrics_schedules")

    op.drop_index("ix_post_metrics_collected_at", table_name="post_metrics")
    op.drop_index("ix_post_metrics_post_id", table_name="post_metrics")
    op.drop_table("post_metrics")

    op.drop_index("ix_posts_posted_at", table_name="posts")
    op.drop_index("ix_posts_agent_id", table_name="posts")
    op.drop_table("posts")

    op.drop_index("ix_target_accounts_agent_id", table_name="target_accounts")
    op.drop_table("target_accounts")

    op.drop_index("ix_experiments_agent_id", table_name="experiments")
    op.drop_table("experiments")

    op.drop_index("ix_account_knowledges_account_id", table_name="account_knowledges")
    op.drop_table("account_knowledges")

    op.drop_index("ix_agents_account_id", table_name="agents")
    op.drop_table("agents")

    op.drop_table("accounts")

    bind = op.get_bind()
    action_type.drop(bind, checkfirst=True)
    experiment_status.drop(bind, checkfirst=True)
    metrics_schedule_type.drop(bind, checkfirst=True)
    metrics_collection_type.drop(bind, checkfirst=True)
    post_type.drop(bind, checkfirst=True)
    agent_status.drop(bind, checkfirst=True)
    account_type.drop(bind, checkfirst=True)
