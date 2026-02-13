from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import BudgetExceededError, BudgetLedger, ExternalPost, ExternalPostMetrics, RateLimiter, XClient
from core.db import Base, SessionLocal, engine
from core.models import (
    ActionType,
    Account,
    AccountType,
    Agent,
    AgentStatus,
    DailyPDCA,
    MetricsCollectionType,
    Post,
    PostMetrics,
    PostType,
)


class FakeXClient:
    def list_posts(self, agent_id: int, target_date: date) -> list[ExternalPost]:
        base = datetime(target_date.year, target_date.month, target_date.day, 9, tzinfo=timezone.utc)
        return [
            ExternalPost(
                external_id=f"{agent_id}-{target_date.isoformat()}-001",
                posted_at=base,
                text="Daily update alpha",
                type=PostType.tweet,
            ),
            ExternalPost(
                external_id=f"{agent_id}-{target_date.isoformat()}-002",
                posted_at=base + timedelta(hours=2),
                text="Daily update beta",
                type=PostType.thread,
                media_urls=["https://example.com/image1.png"],
            ),
            ExternalPost(
                external_id=f"{agent_id}-{target_date.isoformat()}-003",
                posted_at=base + timedelta(hours=4),
                text="Daily update gamma",
                type=PostType.quote_rt,
            ),
        ]

    def get_post_metrics(self, external_post: ExternalPost) -> ExternalPostMetrics:
        seed = sum(ord(c) for c in external_post.external_id)
        likes = 10 + seed % 50
        replies = 2 + seed % 8
        retweets = 3 + seed % 12
        clicks = 15 + seed % 60
        impressions = likes * 20 + replies * 30 + retweets * 25 + clicks * 10
        return ExternalPostMetrics(
            external_id=external_post.external_id,
            impressions=impressions,
            likes=likes,
            replies=replies,
            retweets=retweets,
            clicks=clicks,
        )


def _ensure_agent(session: Session, agent_id: int) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent:
        return agent

    account = Account(
        name=f"agent-{agent_id}",
        type=AccountType.business,
        api_keys={"x": "fake"},
        media_assets_path="/tmp",
    )
    session.add(account)
    session.flush()

    agent = Agent(id=agent_id, account_id=account.id, status=AgentStatus.active, feature_toggles={})
    session.add(agent)
    session.flush()
    return agent


def _upsert_post(session: Session, agent_id: int, external_post: ExternalPost) -> Post:
    existing = session.scalar(
        select(Post).where(Post.agent_id == agent_id, Post.external_id == external_post.external_id)
    )
    if existing:
        existing.content = external_post.text
        existing.posted_at = external_post.posted_at
        existing.type = external_post.type
        existing.media_urls = external_post.media_urls
        return existing

    post = Post(
        agent_id=agent_id,
        external_id=external_post.external_id,
        content=external_post.text,
        posted_at=external_post.posted_at,
        type=external_post.type,
        media_urls=external_post.media_urls,
    )
    session.add(post)
    session.flush()
    return post


def _save_confirmed_metrics(
    session: Session,
    post: Post,
    metrics: ExternalPostMetrics,
    collected_at: datetime,
) -> bool:
    exists = session.scalar(
        select(PostMetrics).where(
            PostMetrics.post_id == post.id,
            PostMetrics.collection_type == MetricsCollectionType.confirmed,
        )
    )
    if exists:
        return False

    session.add(
        PostMetrics(
            post_id=post.id,
            collection_type=MetricsCollectionType.confirmed,
            collected_at=collected_at,
            impressions=metrics.impressions,
            likes=metrics.likes,
            replies=metrics.replies,
            retweets=metrics.retweets,
            clicks=metrics.clicks,
            engagements=metrics.likes + metrics.replies + metrics.retweets + metrics.clicks,
        )
    )
    return True


def run_daily_routine(agent_id: int, base_date: date, x_client: XClient | None = None) -> dict[str, object]:
    target_date = base_date - timedelta(days=2)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    x_client = x_client or FakeXClient()

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        agent = _ensure_agent(session, agent_id)
        x_cost = Decimal("1.00")
        llm_cost = Decimal("2.00")
        ledger = BudgetLedger(
            session,
            agent_id=agent_id,
            target_date=target_date,
            daily_budget=agent.daily_budget,
            split_x=agent.budget_split_x,
            split_llm=agent.budget_split_llm,
        )
        rate_limiter = RateLimiter(session, agent_id=agent_id, target_date=target_date, daily_total_limit=3)

        if rate_limiter.is_limited(action_type=ActionType.reply, requested=1):
            rate_status = rate_limiter.status(action_type=ActionType.reply)
            budget_status = ledger.status()
            pdca = session.scalar(
                select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date)
            )
            if pdca is None:
                pdca = DailyPDCA(
                    agent_id=agent_id,
                    date=target_date,
                    analytics_summary={"status": "skip", "reason": "rate_limited"},
                    analysis={"status": "skipped", "reason": "rate_limited"},
                    strategy={"next_action": "wait"},
                    posts_created=[],
                )
                session.add(pdca)
            session.commit()
            return {
                "target_date": target_date,
                "log_path": None,
                "posts": 0,
                "status": "skip",
                "reason": "rate_limited",
                "budget_status": {"total_spent": str(budget_status.total_spent), "daily_limit": str(budget_status.daily_limit)},
                "rate_status": rate_status,
            }

        try:
            ledger.reserve(x_cost=x_cost, llm_cost=llm_cost)
        except BudgetExceededError:
            rate_status = rate_limiter.status(action_type=ActionType.reply)
            budget_status = ledger.status()
            pdca = session.scalar(
                select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date)
            )
            if pdca is None:
                pdca = DailyPDCA(
                    agent_id=agent_id,
                    date=target_date,
                    analytics_summary={"status": "skip", "reason": "budget_exceeded"},
                    analysis={"status": "skipped", "reason": "budget_exceeded"},
                    strategy={"next_action": "wait"},
                    posts_created=[],
                )
                session.add(pdca)
            session.commit()
            return {
                "target_date": target_date,
                "log_path": None,
                "posts": 0,
                "status": "skip",
                "reason": "budget_exceeded",
                "budget_status": {
                    "total_spent": str(budget_status.total_spent),
                    "daily_limit": str(budget_status.daily_limit),
                    "reserved_total": str(budget_status.total_reserved),
                },
                "rate_status": rate_status,
            }

        external_posts = x_client.list_posts(agent_id=agent_id, target_date=target_date)
        inserted_metrics = 0
        metric_rows: list[dict[str, object]] = []
        post_ids: list[int] = []

        for external_post in external_posts:
            post = _upsert_post(session, agent_id, external_post)
            post_ids.append(post.id)
            external_metrics = x_client.get_post_metrics(external_post)
            created = _save_confirmed_metrics(session, post, external_metrics, now)
            if created:
                inserted_metrics += 1
            metric_rows.append(asdict(external_metrics))

        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date))
        analytics_summary = {
            "target_date": target_date.isoformat(),
            "post_count": len(external_posts),
            "confirmed_metrics_created": inserted_metrics,
        }
        if pdca is None:
            pdca = DailyPDCA(
                agent_id=agent_id,
                date=target_date,
                analytics_summary=analytics_summary,
                analysis={"status": "completed"},
                strategy={"next_action": "continue"},
                posts_created=[{"external_id": p.external_id} for p in external_posts],
            )
            session.add(pdca)
        else:
            pdca.analytics_summary = analytics_summary

        ledger.commit()

        budget_status = ledger.status()
        rate_status = rate_limiter.status(action_type=ActionType.reply)

        session.commit()

    log_payload = {
        "agent_id": agent_id,
        "base_date": base_date.isoformat(),
        "target_date": target_date.isoformat(),
        "posts": post_ids,
        "metrics": metric_rows,
        "confirmed_metrics_created": inserted_metrics,
        "cost": {"x_api_cost": str(x_cost), "llm_cost": str(llm_cost), "total": str(x_cost + llm_cost)},
    }
    log_dir = Path("apps/worker/logs") / str(agent_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{target_date.isoformat()}.json"
    log_path.write_text(json.dumps(log_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    return {
        "target_date": target_date,
        "log_path": log_path,
        "posts": len(post_ids),
        "status": "success",
        "budget_status": {
            "total_spent": str(budget_status.total_spent),
            "daily_limit": str(budget_status.daily_limit),
        },
        "rate_status": rate_status,
    }
