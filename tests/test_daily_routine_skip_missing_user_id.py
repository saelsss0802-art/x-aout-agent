from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from apps.worker import daily_routine
from apps.worker.daily_routine import MissingXUserIdError
from core import ExternalPost, ExternalPostMetrics, XUsage
from core.db import Base
from core.models import CostLog, DailyPDCA


class MissingUserClient:
    def resolve_user_id(self, handle_or_me: str = "me") -> str:
        raise MissingXUserIdError("Please set X_USER_ID")

    def list_posts(self, agent_id: int, target_date: date) -> list[ExternalPost]:
        raise MissingXUserIdError("Please set X_USER_ID")

    def get_post_metrics(self, external_post: ExternalPost) -> ExternalPostMetrics:
        return ExternalPostMetrics(external_id=external_post.external_id)

    def get_daily_usage(self, usage_date: date) -> XUsage:
        return XUsage(usage_date=usage_date, units=0, raw={})


def test_daily_routine_skips_when_user_id_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)

    result = daily_routine.run_daily_routine(agent_id=77, base_date=date(2026, 1, 10), x_client=MissingUserClient())

    assert result["status"] == "skip"
    assert result["reason"] == "missing_user_id"

    with Session(engine) as session:
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 77, DailyPDCA.date == date(2026, 1, 8)))
        costs = session.scalars(select(CostLog).where(CostLog.agent_id == 77, CostLog.date == date(2026, 1, 8))).all()

    assert pdca is not None
    assert pdca.analytics_summary["reason"] == "missing_user_id"
    assert costs == []
