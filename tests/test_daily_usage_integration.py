from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("httpx")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.worker.daily_routine import _apply_usage
from core import XUsage
from core.db import Base
from core.models import CostLog


class UsageClient:
    def get_daily_usage(self, usage_date: date) -> XUsage:
        return XUsage(usage_date=usage_date, units=12, raw={"data": {"usage": 12}})


def test_apply_usage_updates_cost_log(monkeypatch) -> None:
    monkeypatch.setenv("USE_X_USAGE", "1")
    monkeypatch.setenv("X_UNIT_PRICE", "0.5")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        session.add(
            CostLog(
                agent_id=10,
                date=date(2026, 1, 8),
                x_api_cost=Decimal("1.00"),
                llm_cost=Decimal("2.00"),
                image_gen_cost=Decimal("0"),
                total=Decimal("3.00"),
            )
        )
        session.flush()

        _apply_usage(session, agent_id=10, usage_date=date(2026, 1, 8), x_client=UsageClient())
        session.commit()

        row = session.scalar(select(CostLog).where(CostLog.agent_id == 10, CostLog.date == date(2026, 1, 8)))

    assert row is not None
    assert row.x_usage_units == 12
    assert row.x_api_cost == Decimal("6.00")
    assert row.total == Decimal("8.00")
    assert row.x_usage_raw["data"]["usage"] == 12
