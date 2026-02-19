from __future__ import annotations

from datetime import date

import pytest

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


def test_patch_agent_updates_budget_and_logs_audit(tmp_path) -> None:
    test_session = _make_test_session_factory(tmp_path)
    agent_id = _seed_minimum_data(test_session)
    main.SessionLocal = test_session

    client = TestClient(main.app)
    response = client.patch(f"/api/agents/{agent_id}", json={"daily_budget": 420})

    assert response.status_code == 200
    assert response.json()["daily_budget"] == 420
    with test_session() as session:
        agent = session.get(Agent, agent_id)
        assert agent is not None
        assert agent.daily_budget == 420
        count = session.scalar(select(func.count(AuditLog.id)).where(AuditLog.agent_id == agent_id))
        assert count == 1


def test_patch_agent_merges_feature_toggles_and_keeps_existing_keys(tmp_path) -> None:
    test_session = _make_test_session_factory(tmp_path)
    agent_id = _seed_minimum_data(test_session)
    main.SessionLocal = test_session

    client = TestClient(main.app)
    response = client.patch(
        f"/api/agents/{agent_id}",
        json={"feature_toggles": {"posts_per_day": 2, "auto_post": True}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["feature_toggles"]["posts_per_day"] == 2
    assert payload["feature_toggles"]["auto_post"] is True
    assert payload["feature_toggles"]["posting"] is True

    with test_session() as session:
        logs = session.scalars(select(AuditLog).where(AuditLog.agent_id == agent_id).order_by(AuditLog.id.asc())).all()
        assert len(logs) == 1
        assert logs[0].status == "success"
        assert logs[0].event_type == "agent_update"


@pytest.mark.parametrize("body", [{}, {"daily_budget": -1}])
def test_patch_agent_rejects_invalid_payload(body, tmp_path) -> None:
    test_session = _make_test_session_factory(tmp_path)
    agent_id = _seed_minimum_data(test_session)
    main.SessionLocal = test_session

    client = TestClient(main.app)
    response = client.patch(f"/api/agents/{agent_id}", json=body)
    assert response.status_code == 400
