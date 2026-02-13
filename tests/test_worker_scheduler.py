from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import Base, engine
from core.models import Account, AccountType, Agent, AgentStatus, DailyPDCA

from apps.worker import scheduler


def _create_agent(session: Session, *, agent_id: int, status: AgentStatus) -> Agent:
    account = Account(
        name=f"account-{agent_id}",
        type=AccountType.business,
        api_keys={"x": "fake"},
        media_assets_path="/tmp",
    )
    session.add(account)
    session.flush()
    agent = Agent(id=agent_id, account_id=account.id, status=status, feature_toggles={})
    session.add(agent)
    return agent


def test_run_all_agents_runs_only_active_and_continues_on_error(monkeypatch) -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        _create_agent(session, agent_id=1, status=AgentStatus.active)
        _create_agent(session, agent_id=2, status=AgentStatus.active)
        _create_agent(session, agent_id=3, status=AgentStatus.paused)
        session.commit()

    called: list[int] = []

    def fake_run_daily_routine(agent_id: int, base_date: date, x_client=None):
        called.append(agent_id)
        if agent_id == 2:
            raise RuntimeError("boom")
        return {
            "target_date": base_date,
            "log_path": f"apps/worker/logs/{agent_id}/{base_date.isoformat()}.json",
            "posts": 0,
        }

    monkeypatch.setattr(scheduler, "run_daily_routine", fake_run_daily_routine)

    results = scheduler.run_all_agents(base_date=date(2026, 1, 5))

    assert called == [1, 2]
    assert [r["agent_id"] for r in results] == [1, 2]
    assert results[0]["status"] == "success"
    assert results[1]["status"] == "failed"

    with Session(engine) as session:
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 2))

    assert pdca is not None
    assert pdca.analytics_summary["error"]["type"] == "RuntimeError"
