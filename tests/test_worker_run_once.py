from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.models import (
    Account,
    AccountType,
    ActionType,
    Agent,
    AgentStatus,
    CostLog,
    DailyPDCA,
    EngagementAction,
    MetricsCollectionType,
    Post,
    PostMetrics,
    TargetAccount,
)


ROOT = Path(__file__).resolve().parents[1]


def _run_once(db_url: str, agent_id: int, run_date: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    env["PYTHONPATH"] = "packages/core"
    return subprocess.run(
        [sys.executable, "-m", "apps.worker.run_once", "--agent-id", str(agent_id), "--date", run_date],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


def test_run_once_creates_daily_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    db_url = f"sqlite+pysqlite:///{db_path}"
    run_date = "2026-01-05"
    target_date = "2026-01-03"

    result = _run_once(db_url=db_url, agent_id=11, run_date=run_date, cwd=ROOT)
    assert result.returncode == 0, result.stderr

    engine = create_engine(db_url, future=True)
    with Session(engine) as session:
        posts = session.scalars(select(Post).where(Post.agent_id == 11)).all()
        metrics = session.scalars(
            select(PostMetrics).where(PostMetrics.collection_type == MetricsCollectionType.confirmed)
        ).all()
        pdca = session.scalars(select(DailyPDCA).where(DailyPDCA.agent_id == 11)).all()
        cost_logs = session.scalars(select(CostLog).where(CostLog.agent_id == 11)).all()

    assert len(posts) == 3
    assert len(metrics) == 3
    assert len(pdca) == 1
    assert len(cost_logs) == 1

    log_path = ROOT / "apps" / "worker" / "logs" / "11" / f"{target_date}.json"
    assert log_path.exists()
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["target_date"] == target_date
    assert payload["confirmed_metrics_created"] == 3


def test_run_once_is_idempotent_for_confirmed_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "worker-idempotent.db"
    db_url = f"sqlite+pysqlite:///{db_path}"
    run_date = "2026-01-06"

    first = _run_once(db_url=db_url, agent_id=21, run_date=run_date, cwd=ROOT)
    assert first.returncode == 0, first.stderr

    second = _run_once(db_url=db_url, agent_id=21, run_date=run_date, cwd=ROOT)
    assert second.returncode == 0, second.stderr

    engine = create_engine(db_url, future=True)
    with Session(engine) as session:
        posts = session.scalars(select(Post).where(Post.agent_id == 21)).all()
        metrics = session.scalars(
            select(PostMetrics).where(PostMetrics.collection_type == MetricsCollectionType.confirmed)
        ).all()

    assert len(posts) == 3
    assert len(metrics) == 3


def test_run_daily_routine_skips_when_budget_exceeded(tmp_path: Path) -> None:
    db_path = tmp_path / "worker-budget.db"
    db_url = f"sqlite+pysqlite:///{db_path}"
    run_date = "2026-01-10"
    target_date = "2026-01-08"

    engine = create_engine(db_url, future=True)
    from core.db import Base

    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        account = Account(name="a", type=AccountType.business, api_keys={"x": "fake"}, media_assets_path="/tmp")
        session.add(account)
        session.flush()
        agent = Agent(
            id=31,
            account_id=account.id,
            status=AgentStatus.active,
            feature_toggles={},
            daily_budget=2,
            budget_split_x=1,
            budget_split_llm=1,
        )
        session.add(agent)
        session.commit()

    result = _run_once(db_url=db_url, agent_id=31, run_date=run_date, cwd=ROOT)
    assert result.returncode == 0, result.stderr

    with Session(engine) as session:
        logs = session.scalars(select(CostLog).where(CostLog.agent_id == 31)).all()
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 31))

    assert logs == []
    assert pdca is not None
    assert pdca.analysis["reason"] == "budget_exceeded"


def test_run_daily_routine_skips_when_rate_limited(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    db_path = tmp_path / "worker-rate.db"
    db_url = f"sqlite+pysqlite:///{db_path}"
    run_date = "2026-01-10"

    engine = create_engine(db_url, future=True)
    from core.db import Base

    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        account = Account(name="b", type=AccountType.business, api_keys={"x": "fake"}, media_assets_path="/tmp")
        session.add(account)
        session.flush()
        agent = Agent(id=41, account_id=account.id, status=AgentStatus.active, feature_toggles={})
        session.add(agent)
        session.flush()
        target = TargetAccount(agent_id=agent.id, handle="x", like_limit=5, reply_limit=5, quote_rt_limit=5)
        session.add(target)
        session.flush()
        for idx in range(3):
            session.add(
                EngagementAction(
                    agent_id=agent.id,
                    target_account_id=target.id,
                    action_type=ActionType.reply,
                    target_post_url=f"https://example.com/{idx}",
                    content="hi",
                    executed_at=datetime(2026, 1, 8, 1 + idx, tzinfo=timezone.utc),
                )
            )
        session.commit()

    result = _run_once(db_url=db_url, agent_id=41, run_date=run_date, cwd=ROOT)
    assert result.returncode == 0, result.stderr

    with Session(engine) as session:
        logs = session.scalars(select(CostLog).where(CostLog.agent_id == 41)).all()
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 41))

    assert logs == []
    assert pdca is not None
    assert pdca.analysis["reason"] == "rate_limited"
