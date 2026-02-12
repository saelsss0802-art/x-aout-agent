from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from core.db import Base
from core.models import (
    Account,
    AccountType,
    Agent,
    AgentStatus,
    MetricsCollectionType,
    Post,
    PostMetrics,
    PostType,
)


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine) as test_session:
        yield test_session


def _create_account_and_agent(session: Session) -> Agent:
    account = Account(
        name="A",
        type=AccountType.business,
        api_keys={"x": "key"},
        media_assets_path="/tmp",
    )
    session.add(account)
    session.flush()

    agent = Agent(account_id=account.id, status=AgentStatus.active, feature_toggles={})
    session.add(agent)
    session.flush()
    return agent


def test_enum_validation_rejects_unknown_status(session: Session) -> None:
    account = Account(
        name="A",
        type=AccountType.business,
        api_keys={"x": "key"},
        media_assets_path="/tmp",
    )
    session.add(account)
    session.flush()

    agent = Agent(account_id=account.id, status="invalid", feature_toggles={})  # type: ignore[arg-type]
    session.add(agent)

    with pytest.raises(StatementError):
        session.flush()


def test_required_field_missing_raises_error(session: Session) -> None:
    account = Account(
        type=AccountType.individual,
        api_keys={"x": "key"},
        media_assets_path="/tmp",
    )  # type: ignore[call-arg]
    session.add(account)

    with pytest.raises(IntegrityError):
        session.flush()


def test_fk_constraint_for_posts(session: Session) -> None:
    post = Post(agent_id=9999, content="hello", type=PostType.tweet, media_urls=[])
    session.add(post)

    with pytest.raises(IntegrityError):
        session.flush()


def test_agent_budget_defaults(session: Session) -> None:
    agent = _create_account_and_agent(session)

    assert agent.daily_budget == 300
    assert agent.budget_split_x == 100
    assert agent.budget_split_llm == 200


def test_post_metrics_unique_snapshot(session: Session) -> None:
    agent = _create_account_and_agent(session)
    post = Post(agent_id=agent.id, content="hello", type=PostType.tweet, media_urls=[])
    session.add(post)
    session.flush()

    collected_at = datetime(2026, 1, 1, 0, 0, 0)
    session.add(
        PostMetrics(
            post_id=post.id,
            collection_type=MetricsCollectionType.snapshot,
            collected_at=collected_at,
        )
    )
    session.flush()

    session.add(
        PostMetrics(
            post_id=post.id,
            collection_type=MetricsCollectionType.snapshot,
            collected_at=collected_at,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_post_type_rejects_old_value(session: Session) -> None:
    agent = _create_account_and_agent(session)
    post = Post(agent_id=agent.id, content="legacy", type="post", media_urls=[])  # type: ignore[arg-type]
    session.add(post)

    with pytest.raises(StatementError):
        session.flush()
