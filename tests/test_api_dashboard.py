from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from apps.api.app import main
from core.db import Base
from core.models import Account, AccountType, Agent, AgentStatus, AuditLog, CostLog


def _make_test_session_factory(tmp_path):
    db_path = tmp_path / "dashboard.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}", future=True)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal


def _seed_minimum_data(session_factory):
    with session_factory() as session:
        account = Account(name="acct", type=AccountType.individual, api_keys={}, media_assets_path="/tmp")
        session.add(account)
        session.flush()
        agent = Agent(account_id=account.id, status=AgentStatus.active, feature_toggles={"posting": True}, daily_budget=300)
        session.add(agent)
        session.flush()
        session.add(
            CostLog(
                agent_id=agent.id,
                date=date.today(),
                x_api_cost=0,
                x_api_cost_estimate=12,
                llm_cost=3,
                image_gen_cost=0,
                total=15,
                x_usage_units=120,
            )
        )
        session.commit()
        return agent.id


def test_agents_list_returns_data(tmp_path) -> None:
    test_session = _make_test_session_factory(tmp_path)
    _seed_minimum_data(test_session)
    main.SessionLocal = test_session

    client = TestClient(main.app)
    response = client.get("/api/agents")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["agents"]) == 1
    assert payload["agents"][0]["today_cost"]["total"] == 15.0


def test_stop_resume_updates_agent_and_audit(tmp_path) -> None:
    test_session = _make_test_session_factory(tmp_path)
    agent_id = _seed_minimum_data(test_session)
    main.SessionLocal = test_session

    client = TestClient(main.app)
    stop_res = client.post(f"/api/agents/{agent_id}/stop", json={"reason": "manual override"})
    assert stop_res.status_code == 200

    with test_session() as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.stopped
        assert agent.stop_reason == "manual override"
        count_after_stop = session.scalar(select(func.count(AuditLog.id)).where(AuditLog.agent_id == agent_id))
        assert count_after_stop == 1

    resume_res = client.post(f"/api/agents/{agent_id}/resume")
    assert resume_res.status_code == 200

    with test_session() as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.status == AgentStatus.active
        assert agent.stop_reason is None
        count_after_resume = session.scalar(select(func.count(AuditLog.id)).where(AuditLog.agent_id == agent_id))
        assert count_after_resume == 2
