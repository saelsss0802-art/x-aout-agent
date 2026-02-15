from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from .db.models import PostType


@dataclass(frozen=True)
class ExternalPost:
    external_id: str
    posted_at: datetime
    text: str
    type: PostType
    media_urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExternalPostMetrics:
    external_id: str
    impressions: int = 0
    likes: int = 0
    replies: int = 0
    retweets: int = 0
    clicks: int = 0
    impressions_unavailable: bool = False


@dataclass(frozen=True)
class XUsage:
    usage_date: date
    units: int
    raw: dict[str, object] = field(default_factory=dict)


class XClient(Protocol):
    def resolve_user_id(self, handle_or_me: str = "me") -> str: ...

    def list_posts(self, agent_id: int, target_date: date) -> list[ExternalPost]: ...

    def get_post_metrics(self, external_post: ExternalPost) -> ExternalPostMetrics: ...

    def get_daily_usage(self, usage_date: date) -> XUsage: ...
