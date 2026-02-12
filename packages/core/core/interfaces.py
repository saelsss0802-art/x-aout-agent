from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


class WorkerJob(Protocol):
    def __call__(self) -> None: ...


@dataclass(frozen=True)
class ExternalPost:
    external_id: str
    posted_at: datetime
    content: str
    type: str
    media_url: str | None = None


@dataclass(frozen=True)
class ExternalPostMetrics:
    impressions: int = 0
    engagements: int = 0
    likes: int = 0
    replies: int = 0
    retweets: int = 0
    clicks: int = 0


class XClient(Protocol):
    def list_posts(self, agent: object, target_date: date) -> list[ExternalPost]: ...

    def get_confirmed_metrics(self, external_post_ids: list[str]) -> dict[str, ExternalPostMetrics]: ...
