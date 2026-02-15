from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

httpx = pytest.importorskip("httpx")

from core import ExternalPost
from core.models import PostType

from apps.worker.real_x_client import MissingXUserIdError, RealXClient


def _build_client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport, timeout=5.0)


def test_real_x_client_calls_expected_endpoints() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/users/42/tweets"):
            return httpx.Response(
                status_code=200,
                json={
                    "data": [
                        {
                            "id": "123",
                            "text": "hello",
                            "created_at": "2026-01-08T01:00:00Z",
                            "attachments": {"media_keys": ["m1"]},
                        }
                    ],
                    "includes": {"media": [{"media_key": "m1", "url": "https://img.example/a.png"}]},
                },
            )
        if request.url.path.endswith("/tweets"):
            return httpx.Response(
                status_code=200,
                json={"data": [{"id": "123", "public_metrics": {"like_count": 4, "reply_count": 1, "retweet_count": 2}}]},
            )
        return httpx.Response(status_code=404, json={})

    client = RealXClient(bearer_token="token", user_id="42", http_client=_build_client(handler))

    posts = client.list_posts(agent_id=7, target_date=date(2026, 1, 8))
    assert len(posts) == 1
    assert posts[0].external_id == "123"
    assert posts[0].media_urls == ["https://img.example/a.png"]

    metrics = client.get_post_metrics(posts[0])
    assert metrics.likes == 4

    assert calls[0].url.path.endswith("/users/42/tweets")
    assert calls[0].url.params["max_results"] == "100"
    assert calls[0].url.params["start_time"] == "2026-01-08T00:00:00Z"
    assert calls[0].url.params["end_time"] == "2026-01-09T00:00:00Z"
    assert calls[1].url.path.endswith("/tweets")
    assert calls[1].url.params["ids"] == "123"


def test_real_x_client_marks_impression_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"data": [{"id": "123", "public_metrics": {"like_count": 1, "reply_count": 0, "retweet_count": 0}}]},
        )

    client = RealXClient(bearer_token="token", user_id="42", http_client=_build_client(handler))
    post = ExternalPost(
        external_id="123",
        posted_at=datetime(2026, 1, 8, tzinfo=timezone.utc),
        text="hello",
        type=PostType.tweet,
    )

    metrics = client.get_post_metrics(post)

    assert metrics.impressions == 0
    assert metrics.impressions_unavailable is True


def test_resolve_user_id_raises_on_403() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=403, json={"title": "Forbidden"})

    client = RealXClient(bearer_token="token", user_id=None, http_client=_build_client(handler))

    try:
        client.resolve_user_id()
        assert False, "expected MissingXUserIdError"
    except MissingXUserIdError as exc:
        assert "set X_USER_ID" in str(exc)
