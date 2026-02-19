from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from apps.worker import daily_routine, posting_jobs, scheduler
from apps.worker.content_planner import PlanBuildResult, PostDraft
from core.db import Base
from core.models import Account, AccountType, Agent, AgentStatus, AuditLog, DailyPDCA, Post, PostType, XAuthToken


class NoopPoster:
    def post_text(self, agent_id: int, text: str) -> str:
        return f"posted-{agent_id}"


def _seed_agent(session: Session, *, agent_id: int, status: AgentStatus = AgentStatus.active) -> Agent:
    account = Account(name=f"acct-{agent_id}", type=AccountType.business, api_keys={"x": "fake"}, media_assets_path="/tmp")
    session.add(account)
    session.flush()
    agent = Agent(id=agent_id, account_id=account.id, status=status, feature_toggles={})
    session.add(agent)
    session.flush()
    return agent


def test_stopped_agent_is_skipped_in_daily_and_posting(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)
    monkeypatch.setattr(posting_jobs, "SessionLocal", SessionLocal)

    with Session(engine) as session:
        agent = _seed_agent(session, agent_id=41, status=AgentStatus.stopped)
        session.add(
            Post(
                agent_id=agent.id,
                content="stopped post",
                type=PostType.tweet,
                media_urls=[],
                scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        session.commit()

    daily_result = daily_routine.run_daily_routine(agent_id=41, base_date=date(2026, 1, 10))
    posting_result = posting_jobs.run_posting_jobs(base_datetime=datetime.now(timezone.utc), poster=NoopPoster())

    assert daily_result["status"] == "skip"
    assert daily_result["reason"] == "agent_stopped"
    assert posting_result[0]["status"] == "skipped"
    assert posting_result[0]["reason"] == "agent_stopped"


def test_scheduler_excludes_stopped_agents(monkeypatch) -> None:
    Base.metadata.drop_all(bind=scheduler.engine)
    Base.metadata.create_all(bind=scheduler.engine)

    with Session(scheduler.engine) as session:
        _seed_agent(session, agent_id=1, status=AgentStatus.active)
        _seed_agent(session, agent_id=2, status=AgentStatus.stopped)
        session.commit()

    called: list[int] = []

    def fake_run_daily_routine(agent_id: int, base_date: date, x_client=None):
        called.append(agent_id)
        return {"target_date": base_date, "log_path": None, "status": "success"}

    monkeypatch.setattr(scheduler, "run_daily_routine", fake_run_daily_routine)
    results = scheduler.run_all_agents(base_date=date(2026, 1, 10))

    assert called == [1]
    assert [item["agent_id"] for item in results] == [1]


def test_duplicate_content_hash_does_not_create_more_scheduled_posts(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)

    def fixed_plan(*args, **kwargs):
        return PlanBuildResult(drafts=[PostDraft(type=PostType.tweet, text="Same content")], used_search_material=False)

    monkeypatch.setattr(daily_routine, "build_post_drafts", fixed_plan)
    monkeypatch.setenv("POSTS_PER_DAY", "1")

    daily_routine.run_daily_routine(agent_id=77, base_date=date(2026, 1, 10))
    daily_routine.run_daily_routine(agent_id=77, base_date=date(2026, 1, 10))

    with Session(engine) as session:
        posts = session.scalars(select(Post).where(Post.agent_id == 77, Post.scheduled_at.is_not(None))).all()

    assert len(posts) == 1


def test_oauth_refresh_failures_trigger_auto_stop_and_audit(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(posting_jobs, "SessionLocal", SessionLocal)
    monkeypatch.setenv("USE_REAL_X", "1")
    monkeypatch.setenv("X_OAUTH_CLIENT_ID", "cid")

    def fail_token_for_agent(self, agent, now):
        raise posting_jobs.XAuthRefreshError("x_auth_refresh_failed")

    monkeypatch.setattr(posting_jobs.AccountTokenProvider, "token_for_agent", fail_token_for_agent)

    with Session(engine) as session:
        agent = _seed_agent(session, agent_id=88)
        session.add(
            XAuthToken(
                account_id=agent.account_id,
                access_token="old",
                refresh_token="refresh",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                scope="tweet.read tweet.write",
                token_type="bearer",
            )
        )
        due = datetime.now(timezone.utc) - timedelta(minutes=1)
        for i in range(3):
            session.add(Post(agent_id=agent.id, content=f"p{i}", type=PostType.tweet, media_urls=[], scheduled_at=due))
        session.commit()

    posting_jobs.run_posting_jobs(base_datetime=datetime.now(timezone.utc))

    with Session(engine) as session:
        agent = session.get(Agent, 88)
        audits = session.scalars(select(AuditLog).where(AuditLog.agent_id == 88).order_by(AuditLog.id.asc())).all()

    assert agent is not None
    assert agent.status == AgentStatus.stopped
    assert any(a.source == "oauth" and a.status == "failed" for a in audits)
    assert any(a.event_type == "auto_stop" for a in audits)
