from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base

JSONType = JSON().with_variant(JSONB, "postgresql")


class AccountType(str, enum.Enum):
    individual = "individual"
    business = "business"


class AgentStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    disabled = "disabled"


class PostType(str, enum.Enum):
    tweet = "tweet"
    thread = "thread"
    quote_rt = "quote_rt"
    poll = "poll"


class MetricsCollectionType(str, enum.Enum):
    snapshot = "snapshot"
    confirmed = "confirmed"


class MetricsScheduleType(str, enum.Enum):
    snapshot = "snapshot"
    confirmed = "confirmed"


class ExperimentStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    completed = "completed"
    cancelled = "cancelled"


class ActionType(str, enum.Enum):
    like = "like"
    reply = "reply"
    quote_rt = "quote_rt"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type", validate_strings=True), nullable=False
    )
    api_keys: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    media_assets_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    agents: Mapped[list["Agent"]] = relationship(back_populates="account")
    knowledges: Mapped[list["AccountKnowledge"]] = relationship(back_populates="account")


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", validate_strings=True), nullable=False
    )
    feature_toggles: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    daily_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    budget_split_x: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    budget_split_llm: Mapped[int] = mapped_column(Integer, nullable=False, default=200, server_default="200")

    account: Mapped[Account] = relationship(back_populates="agents")
    target_accounts: Mapped[list["TargetAccount"]] = relationship(back_populates="agent")
    posts: Mapped[list["Post"]] = relationship(back_populates="agent")


class AccountKnowledge(Base):
    __tablename__ = "account_knowledges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    persona: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(Text, nullable=False)
    ng_items: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    reference_accounts: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)

    account: Mapped[Account] = relationship(back_populates="knowledges")


class TargetAccount(Base):
    __tablename__ = "target_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    handle: Mapped[str] = mapped_column(String(255), nullable=False)
    like_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    reply_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    quote_rt_limit: Mapped[int] = mapped_column(Integer, nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="target_accounts")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_posted_at", "posted_at"),
        UniqueConstraint("agent_id", "external_id", name="uq_posts_agent_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[PostType] = mapped_column(
        Enum(PostType, name="post_type_enum", validate_strings=True), nullable=False
    )
    media_urls: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    experiment_id: Mapped[int | None] = mapped_column(ForeignKey("experiments.id"), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(128), nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="posts")


class PostMetrics(Base):
    __tablename__ = "post_metrics"
    __table_args__ = (
        UniqueConstraint("post_id", "collection_type", "collected_at", name="uq_postmetrics_post_type_time"),
        Index("ix_post_metrics_collected_at", "collected_at"),
        Index("ix_postmetrics_post_type_time", "post_id", "collection_type", "collected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False, index=True)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    engagements: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    retweets: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    collection_type: Mapped[MetricsCollectionType] = mapped_column(
        Enum(MetricsCollectionType, name="metrics_collection_type_enum", validate_strings=True),
        nullable=False,
    )


class MetricsSchedule(Base):
    __tablename__ = "metrics_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    collect_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[MetricsScheduleType] = mapped_column(
        Enum(MetricsScheduleType, name="metrics_schedule_type_enum", validate_strings=True),
        nullable=False,
    )


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(255), nullable=False)
    variants: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    decision_rule: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus, name="experiment_status", validate_strings=True), nullable=False
    )


class DailyPDCA(Base):
    __tablename__ = "daily_pdca"
    __table_args__ = (Index("ix_daily_pdca_date", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    analytics_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    posts_created: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)


class LocalMemory(Base):
    __tablename__ = "local_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    insight: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SharedKnowledge(Base):
    __tablename__ = "shared_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    results: Mapped[str] = mapped_column(Text, nullable=False)
    conclusion: Mapped[str] = mapped_column(Text, nullable=False)
    trust_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    contributed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False)


class EngagementAction(Base):
    __tablename__ = "engagement_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    target_account_id: Mapped[int] = mapped_column(ForeignKey("target_accounts.id"), nullable=False)
    action_type: Mapped[ActionType] = mapped_column(
        Enum(ActionType, name="action_type", validate_strings=True), nullable=False
    )
    target_post_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CostLog(Base):
    __tablename__ = "cost_logs"
    __table_args__ = (Index("ix_cost_logs_date", "date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    x_api_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    llm_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    image_gen_cost: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    x_usage_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    x_usage_raw: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)


class Heartbeat(Base):
    __tablename__ = "heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="api")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
