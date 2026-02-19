from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from apps.api.app import main as api_main
from apps.api.app.oauth import x_oauth
from apps.worker import posting_jobs
from core.db import Base
from core.models import Account, AccountType, Agent, AgentStatus, OAuthState, Post, PostType, XAuthToken


def _setup_db(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(api_main, "SessionLocal", SessionLocal)
    monkeypatch.setattr(posting_jobs, "SessionLocal", SessionLocal)
    return engine


def test_oauth_callback_and_refresh_persist_tokens(monkeypatch) -> None:
    engine = _setup_db(monkeypatch)
    monkeypatch.setenv("X_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("X_OAUTH_REDIRECT_URI", "http://localhost:8000/oauth/x/callback")

    with Session(engine) as session:
        account = Account(name="acct", type=AccountType.business, api_keys={}, media_assets_path="/tmp")
        session.add(account)
        session.flush()
        session.add(
            OAuthState(
                account_id=account.id,
                state="s1",
                code_verifier="v1",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
        )
        session.commit()
        account_id = account.id

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            grant = request.content.decode("utf-8")
            if "grant_type=authorization_code" in grant:
                return httpx.Response(
                    200,
                    json={
                        "access_token": "access-1",
                        "refresh_token": "refresh-1",
                        "expires_in": 3600,
                        "scope": "tweet.write users.read offline.access",
                        "token_type": "bearer",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "access-2",
                    "refresh_token": "refresh-2",
                    "expires_in": 7200,
                    "scope": "tweet.write users.read offline.access",
                    "token_type": "bearer",
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def exchange_patch(*, code: str, code_verifier: str):
        del code, code_verifier
        return x_oauth.exchange_code_for_token(code="c", code_verifier="v", http_client=httpx.Client(transport=transport))

    def refresh_patch(*, refresh_token: str):
        return x_oauth.refresh_access_token(refresh_token=refresh_token, http_client=httpx.Client(transport=transport))

    monkeypatch.setattr(api_main, "exchange_code_for_token", exchange_patch)
    monkeypatch.setattr(api_main, "refresh_access_token", refresh_patch)

    client = TestClient(api_main.app)
    callback_resp = client.get("/oauth/x/callback", params={"state": "s1", "code": "abc"}, follow_redirects=False)
    assert callback_resp.status_code == 302

    with Session(engine) as session:
        token = session.scalar(select(XAuthToken).where(XAuthToken.account_id == account_id))
        assert token is not None
        assert token.access_token == "access-1"
        assert token.refresh_token == "refresh-1"

    refresh_resp = client.post("/oauth/x/refresh", params={"account_id": account_id})
    assert refresh_resp.status_code == 200

    with Session(engine) as session:
        token = session.scalar(select(XAuthToken).where(XAuthToken.account_id == account_id))
        assert token is not None
        assert token.access_token == "access-2"
        assert token.refresh_token == "refresh-2"


def test_oauth_callback_state_mismatch(monkeypatch) -> None:
    _setup_db(monkeypatch)
    client = TestClient(api_main.app)
    response = client.get("/oauth/x/callback", params={"state": "missing", "code": "abc"})
    assert response.status_code == 400


def test_posting_jobs_real_poster_refresh_and_post(monkeypatch) -> None:
    engine = _setup_db(monkeypatch)
    monkeypatch.setenv("USE_REAL_X", "1")
    monkeypatch.setenv("X_OAUTH_CLIENT_ID", "cid")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "scope": "tweet.write users.read offline.access",
                    "token_type": "bearer",
                },
            )
        if request.url.path.endswith("/2/tweets"):
            return httpx.Response(201, json={"data": {"id": "tweet-123"}})
        return httpx.Response(404)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))

    with Session(engine) as session:
        account = Account(name="acct", type=AccountType.business, api_keys={}, media_assets_path="/tmp")
        session.add(account)
        session.flush()
        agent = Agent(account_id=account.id, status=AgentStatus.active, feature_toggles={})
        session.add(agent)
        session.flush()
        session.add(
            XAuthToken(
                account_id=account.id,
                access_token="old",
                refresh_token="refresh",
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                scope="tweet.write users.read offline.access",
                token_type="bearer",
            )
        )
        post = Post(
            agent_id=agent.id,
            content="hello",
            type=PostType.tweet,
            media_urls=[],
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        session.add(post)
        session.commit()
        post_id = post.id
        account_id = account.id

    class MockProvider(posting_jobs.AccountTokenProvider):
        def __init__(self, session):
            super().__init__(session, http_client=mock_client)

    class MockRealPoster(posting_jobs.RealPoster):
        def __init__(self, account_tokens):
            super().__init__(account_tokens, http_client=mock_client)

    monkeypatch.setattr(posting_jobs, "AccountTokenProvider", MockProvider)
    monkeypatch.setattr(posting_jobs, "RealPoster", MockRealPoster)

    result = posting_jobs.run_posting_jobs(base_datetime=datetime.now(timezone.utc))
    assert any(item.get("status") == "posted" for item in result)

    with Session(engine) as session:
        post = session.get(Post, post_id)
        token = session.scalar(select(XAuthToken).where(XAuthToken.account_id == account_id))

    assert post is not None and post.external_id == "tweet-123"
    assert post.posted_at is not None
    assert token is not None and token.access_token == "new-access"


def test_extract_tweet_id_supports_target_url_variants() -> None:
    target_id = "1901234567890123456"
    urls = [
        f"https://x.com/someuser/status/{target_id}",
        f"https://twitter.com/someuser/status/{target_id}",
        f"https://x.com/i/web/status/{target_id}",
        f"https://x.com/someuser/status/{target_id}/photo/1",
        f"https://twitter.com/someuser/status/{target_id}?s=20",
    ]

    for url in urls:
        assert posting_jobs.extract_tweet_id(url) == target_id


def test_posting_jobs_reply_invalid_target_url_is_skipped(monkeypatch) -> None:
    engine = _setup_db(monkeypatch)

    with Session(engine) as session:
        account = Account(name="acct", type=AccountType.business, api_keys={}, media_assets_path="/tmp")
        session.add(account)
        session.flush()
        agent = Agent(account_id=account.id, status=AgentStatus.active, feature_toggles={})
        session.add(agent)
        session.flush()
        post = Post(
            agent_id=agent.id,
            content="reply",
            type=PostType.reply,
            media_urls=[],
            target_post_url="https://example.com/not-a-status-url",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        session.add(post)
        session.commit()
        post_id = post.id

    result = posting_jobs.run_posting_jobs(base_datetime=datetime.now(timezone.utc))
    assert any(item == {"post_id": post_id, "status": "skipped", "reason": "invalid_target_url"} for item in result)

    with Session(engine) as session:
        post = session.get(Post, post_id)

    assert post is not None
    assert post.posted_at is None
    assert post.external_id is None
