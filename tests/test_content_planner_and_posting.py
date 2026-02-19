from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from apps.worker import daily_routine, posting_jobs
from apps.worker.content_planner import build_post_drafts
from core import BudgetLedger
from core.db import Base
from core.models import (
    Account,
    AccountType,
    ActionType,
    Agent,
    AgentStatus,
    DailyPDCA,
    EngagementAction,
    Post,
    PostType,
    SearchLog,
    TargetAccount,
)


class MultiPoster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def post_text(self, agent_id: int, text: str) -> str:
        del text
        self.calls.append(("tweet", agent_id))
        return f"tweet-{len(self.calls)}"

    def post_thread(self, agent_id: int, parts: list[str]) -> str:
        del parts
        self.calls.append(("thread", agent_id))
        return f"thread-{len(self.calls)}"

    def post_reply(self, agent_id: int, target_post_url: str, text: str) -> str:
        del target_post_url, text
        self.calls.append(("reply", agent_id))
        return f"reply-{len(self.calls)}"

    def post_quote_rt(self, agent_id: int, target_post_url: str, text: str) -> str:
        del target_post_url, text
        self.calls.append(("quote_rt", agent_id))
        return f"quote-{len(self.calls)}"


def _setup_agent(session: Session, agent_id: int = 1) -> Agent:
    account = Account(name="acct", type=AccountType.business, api_keys={"x": "fake"}, media_assets_path="/tmp")
    session.add(account)
    session.flush()
    agent = Agent(id=agent_id, account_id=account.id, status=AgentStatus.active, feature_toggles={})
    session.add(agent)
    session.flush()
    return agent


def test_content_planner_url_exposure_rule(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        agent = _setup_agent(session, 77)
        session.add(
            SearchLog(
                agent_id=agent.id,
                date=date(2026, 1, 8),
                source="x",
                query="q",
                results_json={
                    "results": [
                        {"title": "t", "snippet": "s", "url": "https://x.com/example/status/1"},
                    ]
                },
                cost_estimate=Decimal("0"),
            )
        )
        session.commit()

        ledger = BudgetLedger(
            session,
            agent_id=agent.id,
            target_date=date(2026, 1, 8),
            daily_budget=300,
            split_x=100,
            split_llm=200,
        )

        monkeypatch.setenv("PLAN_REPLY_RATIO", "0.5")
        monkeypatch.setenv("PLAN_QUOTE_RATIO", "0.5")
        monkeypatch.setenv("PLAN_THREAD_RATIO", "0")
        monkeypatch.setenv("PLAN_ALLOW_URL_FOR_VALIDATION", "0")
        plan = build_post_drafts(session, agent_id=agent.id, target_date=date(2026, 1, 8), posts_per_day=2, ledger=ledger)
        assert all("http" not in draft.text for draft in plan.drafts)

        monkeypatch.setenv("PLAN_ALLOW_URL_FOR_VALIDATION", "1")
        with_url = build_post_drafts(
            session,
            agent_id=agent.id,
            target_date=date(2026, 1, 8),
            posts_per_day=2,
            ledger=ledger,
        )
        url_allowed = [d for d in with_url.drafts if d.allow_url]
        assert url_allowed
        for draft in url_allowed:
            assert re.search(r"https?://\S+$", draft.text)
            assert len(re.findall(r"https?://\S+", draft.text)) == 1


def test_daily_routine_creates_mixed_post_types(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTS_PER_DAY", "4")
    monkeypatch.setenv("PLAN_THREAD_RATIO", "0.25")
    monkeypatch.setenv("PLAN_REPLY_RATIO", "0.25")
    monkeypatch.setenv("PLAN_QUOTE_RATIO", "0.25")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)

    result = daily_routine.run_daily_routine(agent_id=92, base_date=date(2026, 1, 10))
    assert result["status"] == "success"

    with Session(engine) as session:
        planned = session.scalars(
            select(Post).where(Post.agent_id == 92, Post.scheduled_at.is_not(None), Post.posted_at.is_(None))
        ).all()
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 92, DailyPDCA.date == date(2026, 1, 8)))

    assert len(planned) == 4
    assert {post.type for post in planned} >= {PostType.tweet, PostType.thread}
    assert pdca is not None



def test_daily_routine_plans_without_search_logs(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("POSTS_PER_DAY", "4")
    monkeypatch.setenv("PLAN_THREAD_RATIO", "0.25")
    monkeypatch.setenv("PLAN_REPLY_RATIO", "0.5")
    monkeypatch.setenv("PLAN_QUOTE_RATIO", "0.25")

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(daily_routine, "engine", engine)
    monkeypatch.setattr(daily_routine, "SessionLocal", SessionLocal)

    result = daily_routine.run_daily_routine(agent_id=93, base_date=date(2026, 1, 10))
    assert result["status"] == "success"

    with Session(engine) as session:
        planned = session.scalars(
            select(Post).where(Post.agent_id == 93, Post.scheduled_at.is_not(None), Post.posted_at.is_(None))
        ).all()
        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == 93, DailyPDCA.date == date(2026, 1, 8)))

    assert len(planned) == 4
    assert all(post.type in (PostType.tweet, PostType.thread) for post in planned)
    assert all(post.target_post_url is None for post in planned)
    assert pdca is not None
    assert pdca.analytics_summary.get("used_search_material") is False

def test_posting_jobs_supports_all_types_and_rate_limits(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(posting_jobs, "SessionLocal", SessionLocal)

    with Session(engine) as session:
        agent = _setup_agent(session, 55)
        target = TargetAccount(agent_id=agent.id, handle="x", like_limit=3, reply_limit=3, quote_rt_limit=3)
        session.add(target)
        session.flush()
        for idx in range(3):
            session.add(
                EngagementAction(
                    agent_id=agent.id,
                    target_account_id=target.id,
                    action_type=ActionType.reply,
                    target_post_url=f"https://x.com/existing/{idx}",
                    content="old",
                    executed_at=datetime(2026, 1, 11, 1 + idx, tzinfo=timezone.utc),
                )
            )
        due = datetime(2026, 1, 11, 9, tzinfo=timezone.utc) - timedelta(minutes=10)
        session.add_all(
            [
                Post(agent_id=agent.id, content="tweet", type=PostType.tweet, media_urls=[], scheduled_at=due),
                Post(
                    agent_id=agent.id,
                    content="thread",
                    type=PostType.thread,
                    media_urls=[],
                    thread_parts_json=["t1", "t2"],
                    scheduled_at=due,
                ),
                Post(
                    agent_id=agent.id,
                    content="reply",
                    type=PostType.reply,
                    media_urls=[],
                    target_post_url="https://x.com/reply/1",
                    scheduled_at=due,
                ),
                Post(
                    agent_id=agent.id,
                    content="quote",
                    type=PostType.quote_rt,
                    media_urls=[],
                    target_post_url="https://x.com/quote/1",
                    scheduled_at=due,
                ),
            ]
        )
        session.commit()

    poster = MultiPoster()
    results = posting_jobs.run_posting_jobs(base_datetime=datetime(2026, 1, 11, 9, tzinfo=timezone.utc), poster=poster)

    with Session(engine) as session:
        posts = session.scalars(select(Post).where(Post.agent_id == 55).order_by(Post.id.asc())).all()

    statuses = {item["status"] for item in results}
    assert "posted" in statuses
    assert any(item.get("reason") == "rate_limited" for item in results)
    posted_types = {p.type for p in posts if p.posted_at is not None}
    assert posted_types == {PostType.tweet, PostType.thread}
    assert all(p.posted_at is None for p in posts if p.type in (PostType.reply, PostType.quote_rt))
