from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from core.db import Base, SessionLocal, engine
from core.models import Account, AccountType, Agent, AgentStatus, CostLog, DailyPDCA, MetricsCollectionType, Post, PostMetrics

from apps.worker.daily_routine import run_daily_confirmed_routine


def _seed_active_agent() -> int:
    with SessionLocal.begin() as session:
        account = Account(
            name="worker test",
            type=AccountType.business,
            api_keys={"x": "fake"},
            media_assets_path="/tmp",
        )
        session.add(account)
        session.flush()

        agent = Agent(
            account_id=account.id,
            status=AgentStatus.active,
            feature_toggles={},
            daily_budget=300,
            budget_split_x=100,
            budget_split_llm=200,
        )
        session.add(agent)
        session.flush()
        return agent.id


def test_run_once_creates_records_and_json_log() -> None:
    Base.metadata.create_all(engine)
    agent_id = _seed_active_agent()
    base_date = date(2026, 2, 12)

    result = run_daily_confirmed_routine(agent_id=agent_id, base_date=base_date)

    with SessionLocal() as session:
        posts = session.query(Post).filter(Post.agent_id == agent_id).all()
        metrics = (
            session.query(PostMetrics)
            .join(Post, Post.id == PostMetrics.post_id)
            .filter(Post.agent_id == agent_id, PostMetrics.collection_type == MetricsCollectionType.confirmed)
            .all()
        )
        pdca = session.query(DailyPDCA).filter(DailyPDCA.agent_id == agent_id).all()
        cost_logs = session.query(CostLog).filter(CostLog.agent_id == agent_id).all()

    assert len(posts) == 3
    assert len(metrics) == 3
    assert len(pdca) == 1
    assert len(cost_logs) == 1
    assert cost_logs[0].x_api_cost == Decimal("100")
    assert cost_logs[0].llm_cost == Decimal("200")
    assert Path(result["log_path"]).exists()


def test_run_once_twice_does_not_duplicate_confirmed_metrics() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    agent_id = _seed_active_agent()
    base_date = datetime.now(tz=timezone.utc).date()

    run_daily_confirmed_routine(agent_id=agent_id, base_date=base_date)
    run_daily_confirmed_routine(agent_id=agent_id, base_date=base_date)

    with SessionLocal() as session:
        metric_count = (
            session.query(PostMetrics)
            .join(Post, Post.id == PostMetrics.post_id)
            .filter(Post.agent_id == agent_id, PostMetrics.collection_type == MetricsCollectionType.confirmed)
            .count()
        )
        post_count = session.query(Post).filter(Post.agent_id == agent_id).count()

    assert post_count == 3
    assert metric_count == 3
