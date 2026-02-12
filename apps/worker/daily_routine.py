from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from core.db import SessionLocal
from core.interfaces import ExternalPost, ExternalPostMetrics, XClient
from core.models import Agent, AgentStatus, CostLog, DailyPDCA, MetricsCollectionType, Post, PostMetrics, PostType


class FakeXClient(XClient):
    def list_posts(self, agent: object, target_date: date) -> list[ExternalPost]:
        base = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
        return [
            ExternalPost(
                external_id=f"x_{getattr(agent, 'id', 0)}_001",
                posted_at=base + timedelta(hours=9),
                content="Deterministic morning update",
                type="tweet",
                media_url=None,
            ),
            ExternalPost(
                external_id=f"x_{getattr(agent, 'id', 0)}_002",
                posted_at=base + timedelta(hours=13, minutes=30),
                content="Deterministic afternoon thread",
                type="thread",
                media_url="https://example.com/media/thread-1.jpg",
            ),
            ExternalPost(
                external_id=f"x_{getattr(agent, 'id', 0)}_003",
                posted_at=base + timedelta(hours=19),
                content="Deterministic evening poll",
                type="poll",
                media_url=None,
            ),
        ]

    def get_confirmed_metrics(self, external_post_ids: list[str]) -> dict[str, ExternalPostMetrics]:
        metrics: dict[str, ExternalPostMetrics] = {}
        for idx, external_id in enumerate(external_post_ids, start=1):
            metrics[external_id] = ExternalPostMetrics(
                impressions=1000 + idx * 100,
                engagements=100 + idx * 10,
                likes=50 + idx * 5,
                replies=10 + idx,
                retweets=15 + idx,
                clicks=20 + idx * 2,
            )
        return metrics


def _resolve_target_date(base_date: date | None) -> date:
    today = base_date or datetime.now(tz=timezone.utc).date()
    return today - timedelta(days=2)


def _map_post_type(post_type: str) -> PostType:
    try:
        return PostType(post_type)
    except ValueError:
        return PostType.tweet


def _upsert_post(session, agent_id: int, external_post: ExternalPost) -> Post:
    existing = session.execute(
        select(Post).where(Post.agent_id == agent_id, Post.external_id == external_post.external_id)
    ).scalar_one_or_none()
    if existing:
        existing.content = external_post.content
        existing.type = _map_post_type(external_post.type)
        existing.posted_at = external_post.posted_at
        existing.media_urls = [external_post.media_url] if external_post.media_url else []
        return existing

    post = Post(
        agent_id=agent_id,
        external_id=external_post.external_id,
        content=external_post.content,
        type=_map_post_type(external_post.type),
        posted_at=external_post.posted_at,
        media_urls=[external_post.media_url] if external_post.media_url else [],
    )
    session.add(post)
    session.flush()
    return post


def _write_daily_log(agent_id: int, base_date: date, payload: dict) -> Path:
    log_dir = Path("apps/worker/logs") / str(agent_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{base_date.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return path


def run_daily_confirmed_routine(
    *,
    agent_id: int,
    base_date: date | None = None,
    x_client: XClient | None = None,
) -> dict:
    client = x_client or FakeXClient()
    target_date = _resolve_target_date(base_date)
    now = datetime.now(tz=timezone.utc)

    with SessionLocal.begin() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        if agent.status != AgentStatus.active:
            raise ValueError(f"Agent is not active: {agent_id}")

        estimated_x_cost = Decimal("100")
        estimated_llm_cost = Decimal("200")
        if estimated_x_cost > Decimal(agent.budget_split_x) or estimated_llm_cost > Decimal(agent.budget_split_llm):
            raise ValueError("Budget exceeded for this routine run")

        external_posts = client.list_posts(agent, target_date)
        metrics_by_post = client.get_confirmed_metrics([p.external_id for p in external_posts])

        inserted_metrics = 0
        total_impressions = 0
        total_engagements = 0
        total_likes = 0

        for external_post in external_posts:
            post = _upsert_post(session, agent.id, external_post)
            metrics = metrics_by_post[external_post.external_id]

            existing_metric = session.execute(
                select(PostMetrics.id).where(
                    PostMetrics.post_id == post.id,
                    PostMetrics.collection_type == MetricsCollectionType.confirmed,
                    func.date(PostMetrics.collected_at) == now.date(),
                )
            ).scalar_one_or_none()
            if existing_metric is None:
                session.add(
                    PostMetrics(
                        post_id=post.id,
                        collection_type=MetricsCollectionType.confirmed,
                        collected_at=now,
                        impressions=metrics.impressions,
                        engagements=metrics.engagements,
                        likes=metrics.likes,
                        replies=metrics.replies,
                        retweets=metrics.retweets,
                        clicks=metrics.clicks,
                    )
                )
                inserted_metrics += 1

            total_impressions += metrics.impressions
            total_engagements += metrics.engagements
            total_likes += metrics.likes

        analytics_summary = {
            "target_date": target_date.isoformat(),
            "post_count": len(external_posts),
            "total_impressions": total_impressions,
            "total_engagements": total_engagements,
            "total_likes": total_likes,
            "inserted_confirmed_metrics": inserted_metrics,
        }

        pdca = DailyPDCA(
            agent_id=agent.id,
            date=target_date,
            analytics_summary=analytics_summary,
            analysis={"note": "confirmed metrics captured"},
            strategy={"next_action": "optimize by top-performing topic"},
            posts_created=[
                {
                    "external_id": p.external_id,
                    "posted_at": p.posted_at.isoformat(),
                    "content": p.content,
                    "type": p.type,
                    "media_url": p.media_url,
                }
                for p in external_posts
            ],
        )
        session.add(pdca)

        session.add(
            CostLog(
                agent_id=agent.id,
                date=now.date(),
                x_api_cost=estimated_x_cost,
                llm_cost=estimated_llm_cost,
                image_gen_cost=Decimal("0"),
                total=estimated_x_cost + estimated_llm_cost,
            )
        )

        try:
            session.flush()
        except IntegrityError as exc:
            raise ValueError(f"Failed to write confirmed metrics: {exc}") from exc

        result = {
            "agent_id": agent.id,
            "base_date": (base_date or datetime.now(tz=timezone.utc).date()).isoformat(),
            "target_date": target_date.isoformat(),
            "confirmed_posts": len(external_posts),
            "inserted_metrics": inserted_metrics,
            "analytics_summary": analytics_summary,
        }

    log_path = _write_daily_log(agent_id, base_date or datetime.now(tz=timezone.utc).date(), result)
    result["log_path"] = str(log_path)
    return result
