from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from apps.worker import daily_routine
from apps.worker.usage_reconcile import reconcile_app_usage
from apps.worker.x_usage_client import XUsageClient
from core import UsageReconciler
from core.db import Base
from core.models import CostLog, DailyPDCA


def test_x_usage_client_extracts_units_from_usage_api_payload() -> None:
    payload = {"data": [{"usage": 7}, {"usage": "5"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/2/usage/tweets"
        return httpx.Response(200, json=payload)

    client = XUsageClient(
        bearer_token="token",
        base_url="https://api.x.com/2",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    snapshot = client.fetch_daily_usage(date(2026, 1, 8))

    assert snapshot.units == 12
    assert snapshot.raw == payload


def test_usage_reconciler_upserts_app_wide_cost_log_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        reconciler = UsageReconciler(session, app_agent_id=0, unit_price=Decimal("0.5"))
        row = reconciler.reconcile_x_usage(target_date=date(2026, 1, 8), units=10, raw={"data": {"usage": 10}})
        session.commit()

        saved = session.scalar(select(CostLog).where(CostLog.agent_id == 0, CostLog.date == date(2026, 1, 8)))

    assert row.agent_id == 0
    assert saved is not None
    assert saved.x_usage_units == 10
    assert saved.x_api_cost_actual == Decimal("5.00")


def test_daily_routine_records_usage_failure_in_pdca(monkeypatch, tmp_path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'usage-fail.db'}"
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("USE_X_USAGE", "1")
    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", sessionmaker(bind=engine, future=True))

    def boom(session: Session, *, usage_date: date) -> dict[str, object]:
        raise RuntimeError("usage_down")

    monkeypatch.setattr(daily_routine, "reconcile_app_usage", boom)

    daily_routine.run_daily_routine(agent_id=501, base_date=date(2026, 1, 10))

    with Session(engine) as session:
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 501, DailyPDCA.date == date(2026, 1, 8)))

    assert pdca is not None
    assert pdca.analytics_summary["usage_fetch_failed"] is True
    assert pdca.analytics_summary["usage_error"] == "usage_down"


def test_reconcile_app_usage_writes_app_row(monkeypatch) -> None:
    monkeypatch.setenv("USE_X_USAGE", "1")
    monkeypatch.setenv("X_BEARER_TOKEN", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"usage": 9}})

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        monkeypatch.setattr(
            "apps.worker.usage_reconcile.XUsageClient",
            lambda bearer_token: XUsageClient(
                bearer_token=bearer_token,
                base_url="https://api.x.com/2",
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            ),
        )
        summary = reconcile_app_usage(session, usage_date=date(2026, 1, 8))
        session.commit()
        row = session.scalar(select(CostLog).where(CostLog.agent_id == 0, CostLog.date == date(2026, 1, 8)))

    assert summary["x_usage_reconciled"] is True
    assert summary["x_usage_units"] == 9
    assert row is not None
    assert row.x_usage_units == 9
