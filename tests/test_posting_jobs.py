from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from apps.worker import posting_jobs
from core.db import Base
from core.models import Account, AccountType, Agent, AgentStatus, Post, PostType


class CountingPoster:
    def __init__(self) -> None:
        self.calls = 0

    def post_text(self, agent_id: int, text: str) -> str:
        self.calls += 1
        return f"ext-{agent_id}-{self.calls}"


def _seed_due_post(session: Session) -> int:
    account = Account(name="acct", type=AccountType.business, api_keys={"x": "fake"}, media_assets_path="/tmp")
    session.add(account)
    session.flush()
    agent = Agent(id=55, account_id=account.id, status=AgentStatus.active, feature_toggles={})
    session.add(agent)
    session.flush()
    post = Post(
        agent_id=agent.id,
        content="hello world",
        type=PostType.tweet,
        media_urls=[],
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        posted_at=None,
    )
    session.add(post)
    session.commit()
    return post.id


def test_run_posting_jobs_posts_due_items_once(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(posting_jobs, "SessionLocal", SessionLocal)

    with Session(engine) as session:
        post_id = _seed_due_post(session)

    poster = CountingPoster()
    now = datetime.now(timezone.utc)
    posting_jobs.run_posting_jobs(base_datetime=now, poster=poster)
    posting_jobs.run_posting_jobs(base_datetime=now + timedelta(minutes=1), poster=poster)

    with Session(engine) as session:
        post = session.scalar(select(Post).where(Post.id == post_id))

    assert post is not None
    assert post.posted_at is not None
    assert post.external_id == "ext-55-1"
    assert poster.calls == 1


def test_due_posts_claim_query_uses_skip_locked_for_postgres() -> None:
    stmt = posting_jobs._due_posts_claim_query(
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        batch_size=10,
        for_update_skip_locked=True,
    )

    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "LIMIT 10" in sql
