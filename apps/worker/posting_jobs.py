from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from core import BudgetExceededError, BudgetLedger, Poster, RateLimiter
from core.db import SessionLocal
from core.models import ActionType, Agent, DailyPDCA, Post, PostType

try:
    from .real_x_client import RealXClient
except ModuleNotFoundError:
    RealXClient = None  # type: ignore[assignment]


class FakePoster:
    def _fake_id(self, agent_id: int, post_type: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"fake-{post_type}-{agent_id}-{stamp}"

    def post_text(self, agent_id: int, text: str) -> str:
        del text
        return self._fake_id(agent_id, "tweet")

    def post_thread(self, agent_id: int, parts: list[str]) -> str:
        del parts
        return self._fake_id(agent_id, "thread")

    def post_reply(self, agent_id: int, target_post_url: str, text: str) -> str:
        del target_post_url, text
        return self._fake_id(agent_id, "reply")

    def post_quote_rt(self, agent_id: int, target_post_url: str, text: str) -> str:
        del target_post_url, text
        return self._fake_id(agent_id, "quote")


class RealXPoster:
    def __init__(self, x_client: RealXClient) -> None:
        self._x_client = x_client

    def post_text(self, agent_id: int, text: str) -> str:
        del agent_id
        return self._x_client.post_text(text)

    def post_thread(self, agent_id: int, parts: list[str]) -> str:
        del agent_id
        if not parts:
            raise ValueError("thread_parts_required")
        return self._x_client.post_text("\n".join(parts))

    def post_reply(self, agent_id: int, target_post_url: str, text: str) -> str:
        del agent_id, target_post_url
        return self._x_client.post_text(text)

    def post_quote_rt(self, agent_id: int, target_post_url: str, text: str) -> str:
        del agent_id, target_post_url
        return self._x_client.post_text(text)


def _build_poster() -> Poster:
    if os.getenv("USE_REAL_X") == "1":
        if RealXClient is None:
            raise RuntimeError("httpx is required when USE_REAL_X=1")
        return RealXPoster(RealXClient.from_env())
    return FakePoster()


def _posting_batch_size() -> int:
    raw_value = os.getenv("POSTING_BATCH_SIZE", "10")
    try:
        return max(1, int(raw_value))
    except ValueError:
        return 10


def _due_posts_claim_query(current: datetime, *, batch_size: int, for_update_skip_locked: bool) -> Select[tuple[Post]]:
    stmt = (
        select(Post)
        .where(Post.scheduled_at.is_not(None), Post.scheduled_at <= current, Post.posted_at.is_(None))
        .order_by(Post.scheduled_at.asc(), Post.id.asc())
        .limit(batch_size)
    )
    if for_update_skip_locked:
        stmt = stmt.with_for_update(skip_locked=True)
    return stmt


def _claim_due_posts(session: Session, current: datetime, *, batch_size: int) -> list[Post]:
    dialect_name = session.get_bind().dialect.name
    use_skip_locked = dialect_name == "postgresql"
    stmt = _due_posts_claim_query(current, batch_size=batch_size, for_update_skip_locked=use_skip_locked)
    return session.scalars(stmt).all()


def _log_posting_error(post_id: int, error_payload: dict[str, Any]) -> None:
    print(json.dumps({"event": "posting_job_error", "post_id": post_id, "error": error_payload}, ensure_ascii=True))


def _append_pdca_error(session, agent_id: int, target_date, error_payload: dict[str, Any]) -> None:
    pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date))
    if pdca is None:
        pdca = DailyPDCA(
            agent_id=agent_id,
            date=target_date,
            analytics_summary={"posting_error": error_payload},
            analysis={"status": "posting_failed"},
            strategy={},
            posts_created=[],
        )
        session.add(pdca)
        return

    summary = dict(pdca.analytics_summary or {})
    errors = list(summary.get("posting_errors", []))
    errors.append(error_payload)
    summary["posting_errors"] = errors
    pdca.analytics_summary = summary


def _post_with_type(posting_poster: Poster, post: Post) -> str:
    if post.type == PostType.tweet:
        return posting_poster.post_text(post.agent_id, post.content)
    if post.type == PostType.thread:
        parts = post.thread_parts_json if isinstance(post.thread_parts_json, list) else [post.content]
        return posting_poster.post_thread(post.agent_id, [str(p) for p in parts if str(p).strip()])
    if post.type == PostType.reply:
        if not post.target_post_url:
            raise ValueError("target_post_url_required")
        return posting_poster.post_reply(post.agent_id, post.target_post_url, post.content)
    if post.type == PostType.quote_rt:
        if not post.target_post_url:
            raise ValueError("target_post_url_required")
        return posting_poster.post_quote_rt(post.agent_id, post.target_post_url, post.content)
    raise ValueError(f"unsupported_post_type:{post.type.value}")


def run_posting_jobs(base_datetime: datetime, poster: Poster | None = None) -> list[dict[str, Any]]:
    current = base_datetime if base_datetime.tzinfo else base_datetime.replace(tzinfo=timezone.utc)
    posting_poster = poster or _build_poster()
    batch_size = _posting_batch_size()
    results: list[dict[str, Any]] = []

    with SessionLocal() as session:
        due_posts = _claim_due_posts(session, current, batch_size=batch_size)
        engagement_attempts = 0

        for post in due_posts:
            if post.posted_at is not None:
                continue
            try:
                agent = session.get(Agent, post.agent_id)
                if agent is None:
                    raise RuntimeError("agent_not_found")

                if post.type in (PostType.reply, PostType.quote_rt):
                    limiter = RateLimiter(session, agent_id=post.agent_id, target_date=current.date(), daily_total_limit=3)
                    action_type = ActionType.reply if post.type == PostType.reply else ActionType.quote_rt
                    if limiter.is_limited(action_type=action_type, requested=engagement_attempts + 1):
                        payload = {"type": "rate_limited", "message": "reply_quote_daily_limit_reached"}
                        _append_pdca_error(session, post.agent_id, current.date(), payload)
                        results.append({"post_id": post.id, "status": "skipped", "reason": "rate_limited"})
                        continue
                    engagement_attempts += 1

                ledger = BudgetLedger(
                    session,
                    agent_id=post.agent_id,
                    target_date=current.date(),
                    daily_budget=agent.daily_budget,
                    split_x=agent.budget_split_x,
                    split_llm=agent.budget_split_llm,
                )
                ledger.reserve(x_cost=Decimal("1.00"), llm_cost=Decimal("0.00"))
                external_id = _post_with_type(posting_poster, post)
                post.external_id = external_id
                post.posted_at = current
                ledger.commit()
                session.flush()
                results.append({"post_id": post.id, "status": "posted", "external_id": external_id})
            except BudgetExceededError as exc:
                payload = {"type": type(exc).__name__, "message": str(exc)}
                _append_pdca_error(session, post.agent_id, current.date(), payload)
                _log_posting_error(post.id, payload)
                results.append({"post_id": post.id, "status": "failed", "error": payload})
            except Exception as exc:  # noqa: BLE001
                payload = {"type": type(exc).__name__, "message": str(exc)}
                _append_pdca_error(session, post.agent_id, current.date(), payload)
                _log_posting_error(post.id, payload)
                results.append({"post_id": post.id, "status": "failed", "error": payload})

        session.commit()

    return results
