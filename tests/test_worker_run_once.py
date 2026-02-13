from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core.models import CostLog, DailyPDCA, MetricsCollectionType, Post, PostMetrics


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
