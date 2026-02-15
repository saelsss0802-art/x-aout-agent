from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from apps.worker import daily_routine
from core.db import Base
from core.models import DailyPDCA, SearchLog


def test_daily_routine_persists_search_logs(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)

    result = daily_routine.run_daily_routine(agent_id=88, base_date=date(2026, 1, 10))

    assert result["status"] == "success"

    with Session(engine) as session:
        logs = session.scalars(
            select(SearchLog).where(SearchLog.agent_id == 88, SearchLog.date == date(2026, 1, 8))
        ).all()

    assert len(logs) == 2
    assert {log.source for log in logs} == {"x", "web"}
    assert all(log.results_json for log in logs)


def test_daily_routine_marks_search_rate_limit_skip(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("X_SEARCH_MAX", "0")
    monkeypatch.setenv("WEB_SEARCH_MAX", "0")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)

    result = daily_routine.run_daily_routine(agent_id=89, base_date=date(2026, 1, 10))

    assert result["status"] == "success"

    with Session(engine) as session:
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 89, DailyPDCA.date == date(2026, 1, 8)))
        logs = session.scalars(select(SearchLog).where(SearchLog.agent_id == 89, SearchLog.date == date(2026, 1, 8))).all()

    assert pdca is not None
    assert pdca.analytics_summary["search"]["count"] == 0
    assert {item["reason"] for item in pdca.analytics_summary["search"]["skipped"]} == {"search_rate_limited"}
    assert logs == []


def test_daily_routine_skips_when_gemini_search_fails(monkeypatch, tmp_path) -> None:
    class BrokenGeminiClient:
        def search(self, query: str, k: int) -> list[dict[str, str]]:
            del query, k
            raise RuntimeError("boom")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_GEMINI_WEB_SEARCH", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)
    monkeypatch.setattr(daily_routine, "GeminiWebSearchClient", BrokenGeminiClient)

    result = daily_routine.run_daily_routine(agent_id=90, base_date=date(2026, 1, 10))

    assert result["status"] == "success"

    with Session(engine) as session:
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 90, DailyPDCA.date == date(2026, 1, 8)))
        logs = session.scalars(
            select(SearchLog).where(
                SearchLog.agent_id == 90, SearchLog.date == date(2026, 1, 8), SearchLog.source == "web"
            )
        ).all()

    assert pdca is not None
    assert any(item["reason"] == "gemini_search_failed" for item in pdca.analytics_summary["search"]["skipped"])
    assert pdca.analytics_summary["search"]["usage"]["web_search_status"] == "failed"
    assert logs == []
