from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from core import BudgetExceededError, BudgetLedger, GuardManager, Poster, RateLimiter, build_post_content_hash
from core.db import SessionLocal
from core.models import ActionType, Agent, AgentStatus, AuditLog, DailyPDCA, Post, PostType, XAuthToken

from .real_x_client import RealXClient
from .usage_reconcile import reconcile_app_usage

X_TOKEN_URL = "https://api.x.com/2/oauth2/token"


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


class RealPoster:
    def __init__(self, account_tokens: dict[int, str], http_client: httpx.Client | None = None) -> None:
        self._account_tokens = account_tokens
        self._http_client = http_client or httpx.Client(timeout=15.0)

    def _client_for_agent(self, agent_id: int) -> RealXClient:
        token = self._account_tokens.get(agent_id)
        if not token:
            raise RuntimeError("x_auth_token_not_found")
        return RealXClient(bearer_token=token, http_client=self._http_client)

    def post_text(self, agent_id: int, text: str) -> str:
        return self._client_for_agent(agent_id).create_tweet(text=text)

    def post_thread(self, agent_id: int, parts: list[str]) -> str:
        if not parts:
            raise ValueError("thread_parts_required")
        client = self._client_for_agent(agent_id)
        first_id = client.create_tweet(text=parts[0])
        prev_id = first_id
        for part in parts[1:]:
            prev_id = client.create_tweet(text=part, in_reply_to_tweet_id=prev_id)
        return first_id

    def post_reply(self, agent_id: int, target_post_url: str, text: str) -> str:
        target_id = extract_tweet_id(target_post_url)
        if not target_id:
            raise ValueError("target_post_url_invalid")
        return self._client_for_agent(agent_id).create_tweet(text=text, in_reply_to_tweet_id=target_id)

    def post_quote_rt(self, agent_id: int, target_post_url: str, text: str) -> str:
        target_id = extract_tweet_id(target_post_url)
        if not target_id:
            raise ValueError("target_post_url_invalid")
        return self._client_for_agent(agent_id).create_tweet(text=text, quote_tweet_id=target_id)


class XAuthRefreshError(RuntimeError):
    pass


class InvalidTargetUrlError(ValueError):
    pass


class AccountTokenProvider:
    def __init__(self, session: Session, http_client: httpx.Client | None = None) -> None:
        self._session = session
        self._http_client = http_client or httpx.Client(timeout=15.0)

    def token_for_agent(self, agent: Agent, now: datetime) -> str:
        token = self._session.scalar(select(XAuthToken).where(XAuthToken.account_id == agent.account_id))
        if token is None:
            raise XAuthRefreshError("x_auth_token_not_found")

        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now + timedelta(minutes=2):
            token = self._refresh(token)
        return token.access_token

    def _refresh(self, token: XAuthToken) -> XAuthToken:
        client_id = os.getenv("X_OAUTH_CLIENT_ID")
        if not client_id:
            raise XAuthRefreshError("X_OAUTH_CLIENT_ID is required")
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": client_id,
        }
        client_secret = os.getenv("X_OAUTH_CLIENT_SECRET")
        auth = (client_id, client_secret) if client_secret else None
        try:
            response = self._http_client.post(
                X_TOKEN_URL,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                auth=auth,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise XAuthRefreshError("x_auth_refresh_failed") from exc

        parsed = response.json()
        if not isinstance(parsed, dict):
            raise XAuthRefreshError("x_auth_refresh_invalid_response")

        access_token = parsed.get("access_token")
        refresh_token = parsed.get("refresh_token")
        expires_in = parsed.get("expires_in")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str) or not isinstance(expires_in, (int, float)):
            raise XAuthRefreshError("x_auth_refresh_invalid_payload")

        token.access_token = access_token
        token.refresh_token = refresh_token
        token.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if isinstance(parsed.get("scope"), str):
            token.scope = parsed["scope"]
        if isinstance(parsed.get("token_type"), str):
            token.token_type = parsed["token_type"]
        self._session.flush()
        return token


def _build_poster(account_tokens: dict[int, str] | None = None) -> Poster:
    if os.getenv("USE_REAL_X") == "1":
        return RealPoster(account_tokens or {})
    return FakePoster()


def _agent_toggle_int(agent: Agent, key: str, default: int) -> int:
    toggles = agent.feature_toggles if isinstance(agent.feature_toggles, dict) else {}
    value = toggles.get(key, default)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


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


def _recent_consecutive_failures(
    session: Session,
    *,
    agent_id: int,
    source: str,
    event_type: str,
    limit: int = 3,
 ) -> int:
    session.flush()
    logs = session.scalars(
        select(AuditLog)
        .where(AuditLog.agent_id == agent_id, AuditLog.source == source, AuditLog.event_type == event_type)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()
    if len(logs) < limit:
        return 0
    if all(log.status == "failed" for log in logs):
        return len(logs)
    return 0


def _post_with_type(posting_poster: Poster, post: Post) -> str:
    if post.type == PostType.tweet:
        return posting_poster.post_text(post.agent_id, post.content)
    if post.type == PostType.thread:
        parts = post.thread_parts_json if isinstance(post.thread_parts_json, list) else [post.content]
        return posting_poster.post_thread(post.agent_id, [str(p) for p in parts if str(p).strip()])
    if post.type == PostType.reply:
        if not post.target_post_url:
            raise ValueError("target_post_url_required")
        if not extract_tweet_id(post.target_post_url):
            raise InvalidTargetUrlError("invalid_target_url")
        return posting_poster.post_reply(post.agent_id, post.target_post_url, post.content)
    if post.type == PostType.quote_rt:
        if not post.target_post_url:
            raise ValueError("target_post_url_required")
        if not extract_tweet_id(post.target_post_url):
            raise InvalidTargetUrlError("invalid_target_url")
        return posting_poster.post_quote_rt(post.agent_id, post.target_post_url, post.content)
    raise ValueError(f"unsupported_post_type:{post.type.value}")


def run_posting_jobs(base_datetime: datetime, poster: Poster | None = None) -> list[dict[str, Any]]:
    current = base_datetime if base_datetime.tzinfo else base_datetime.replace(tzinfo=timezone.utc)
    batch_size = _posting_batch_size()
    results: list[dict[str, Any]] = []

    with SessionLocal() as session:
        due_posts = _claim_due_posts(session, current, batch_size=batch_size)
        engagement_attempts = 0
        token_provider = AccountTokenProvider(session)
        tokens_by_agent: dict[int, str] = {}
        guard = GuardManager(session)

        if poster is None and os.getenv("USE_REAL_X") == "1":
            for post in due_posts:
                if post.posted_at is not None:
                    continue
                agent = session.get(Agent, post.agent_id)
                if agent is None:
                    continue
                if not guard.is_agent_runnable(agent, current):
                    reason = "agent_stopped" if agent.status == AgentStatus.stopped else f"agent_status_{agent.status.value}"
                    guard.record_audit(
                        agent_id=agent.id,
                        target_date=current.date(),
                        source="posting_jobs",
                        event_type="posting",
                        status="skipped",
                        reason=reason,
                        payload={"post_id": post.id},
                    )
                    results.append({"post_id": post.id, "status": "skipped", "reason": reason})
                    continue
                try:
                    tokens_by_agent[post.agent_id] = token_provider.token_for_agent(agent, current)
                except XAuthRefreshError:
                    payload = {"type": "XAuthRefreshError", "message": "x_auth_refresh_failed"}
                    _append_pdca_error(session, post.agent_id, current.date(), payload)
                    guard.record_audit(
                        agent_id=post.agent_id,
                        target_date=current.date(),
                        source="oauth",
                        event_type="refresh",
                        status="failed",
                        reason="x_auth_refresh_failed",
                        payload={"post_id": post.id},
                    )
                    if _recent_consecutive_failures(session, agent_id=post.agent_id, source="oauth", event_type="refresh") >= 3:
                        guard.maybe_auto_stop(
                            post.agent_id,
                            now=current,
                            reason="auto_anomaly_oauth_refresh_failures",
                            source="oauth",
                            payload={"threshold": 3},
                        )
                    results.append({"post_id": post.id, "status": "skipped", "reason": "x_auth_refresh_failed"})
            posting_poster = _build_poster(tokens_by_agent)
        else:
            posting_poster = poster or _build_poster()

        for post in due_posts:
            if post.posted_at is not None:
                continue
            if any(item.get("post_id") == post.id and item.get("status") == "skipped" for item in results):
                continue
            try:
                agent = session.get(Agent, post.agent_id)
                if agent is None:
                    raise RuntimeError("agent_not_found")
                if not guard.is_agent_runnable(agent, current):
                    reason = "agent_stopped" if agent.status == AgentStatus.stopped else f"agent_status_{agent.status.value}"
                    guard.record_audit(
                        agent_id=agent.id,
                        target_date=current.date(),
                        source="posting_jobs",
                        event_type="posting",
                        status="skipped",
                        reason=reason,
                        payload={"post_id": post.id},
                    )
                    results.append({"post_id": post.id, "status": "skipped", "reason": reason})
                    continue

                if post.type in (PostType.reply, PostType.quote_rt):
                    limiter = RateLimiter(
                        session,
                        agent_id=post.agent_id,
                        target_date=current.date(),
                        daily_total_limit=_agent_toggle_int(agent, "reply_quote_daily_max", 3),
                    )
                    action_type = ActionType.reply if post.type == PostType.reply else ActionType.quote_rt
                    if limiter.is_limited(action_type=action_type, requested=engagement_attempts + 1):
                        payload = {"type": "rate_limited", "message": "reply_quote_daily_limit_reached"}
                        _append_pdca_error(session, post.agent_id, current.date(), payload)
                        guard.record_audit(
                            agent_id=agent.id,
                            target_date=current.date(),
                            source="posting_jobs",
                            event_type="posting",
                            status="skipped",
                            reason="rate_limited",
                            payload={"post_id": post.id, "type": post.type.value},
                        )
                        results.append({"post_id": post.id, "status": "skipped", "reason": "rate_limited"})
                        continue
                    engagement_attempts += 1

                content_hash = post.content_hash or build_post_content_hash(post.content, post.thread_parts_json)
                post.content_hash = content_hash
                bucket_date = post.content_bucket_date or current.date()
                post.content_bucket_date = bucket_date
                duplicate = session.scalar(
                    select(Post).where(
                        Post.agent_id == post.agent_id,
                        Post.id != post.id,
                        Post.content_hash == content_hash,
                        Post.content_bucket_date == bucket_date,
                        Post.posted_at.is_not(None),
                    )
                )
                if duplicate is not None:
                    guard.record_audit(
                        agent_id=agent.id,
                        target_date=current.date(),
                        source="posting_jobs",
                        event_type="posting",
                        status="skipped",
                        reason="duplicate_content",
                        payload={"post_id": post.id, "duplicate_post_id": duplicate.id},
                    )
                    results.append({"post_id": post.id, "status": "skipped", "reason": "duplicate_content"})
                    continue

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
                guard.record_audit(
                    agent_id=agent.id,
                    target_date=current.date(),
                    source="posting_jobs",
                    event_type="posting",
                    status="success",
                    payload={"post_id": post.id, "external_id": external_id},
                )
                results.append({"post_id": post.id, "status": "posted", "external_id": external_id})
            except InvalidTargetUrlError as exc:
                payload = {"type": "invalid_target_url", "message": str(exc)}
                _append_pdca_error(session, post.agent_id, current.date(), payload)
                _log_posting_error(post.id, payload)
                guard.record_audit(
                    agent_id=post.agent_id,
                    target_date=current.date(),
                    source="posting_jobs",
                    event_type="posting",
                    status="skipped",
                    reason="invalid_target_url",
                    payload={"post_id": post.id},
                )
                results.append({"post_id": post.id, "status": "skipped", "reason": "invalid_target_url"})
            except BudgetExceededError as exc:
                payload = {"type": type(exc).__name__, "message": str(exc)}
                _append_pdca_error(session, post.agent_id, current.date(), payload)
                _log_posting_error(post.id, payload)
                guard.record_audit(
                    agent_id=post.agent_id,
                    target_date=current.date(),
                    source="posting_jobs",
                    event_type="posting",
                    status="failed",
                    reason="budget_exceeded",
                    payload={"post_id": post.id},
                )
                results.append({"post_id": post.id, "status": "failed", "error": payload})
            except Exception as exc:  # noqa: BLE001
                payload = {"type": type(exc).__name__, "message": str(exc)}
                _append_pdca_error(session, post.agent_id, current.date(), payload)
                _log_posting_error(post.id, payload)
                guard.record_audit(
                    agent_id=post.agent_id,
                    target_date=current.date(),
                    source="posting_jobs",
                    event_type="posting",
                    status="failed",
                    reason=type(exc).__name__,
                    payload={"post_id": post.id},
                )
                if _recent_consecutive_failures(session, agent_id=post.agent_id, source="posting_jobs", event_type="posting") >= 3:
                    guard.maybe_auto_stop(
                        post.agent_id,
                        now=current,
                        reason="auto_anomaly_posting_failures",
                        source="posting_jobs",
                        payload={"threshold": 3},
                    )
                results.append({"post_id": post.id, "status": "failed", "error": payload})

        if os.getenv("POSTING_USAGE_RECONCILE") == "1":
            try:
                usage_result = reconcile_app_usage(session, usage_date=current.date())
                guard.record_audit(
                    agent_id=0,
                    target_date=current.date(),
                    source="usage",
                    event_type="reconcile",
                    status="success" if usage_result.get("x_usage_reconciled") else "skipped",
                    reason=None if usage_result.get("x_usage_reconciled") else "usage_disabled",
                    payload={"x_usage_reconciled": bool(usage_result.get("x_usage_reconciled"))},
                )
            except Exception as exc:
                guard.record_audit(
                    agent_id=0,
                    target_date=current.date(),
                    source="usage",
                    event_type="reconcile",
                    status="failed",
                    reason=type(exc).__name__,
                    payload={"message": str(exc)[:120]},
                )

        session.commit()

    return results


def extract_tweet_id(url: str) -> str:
    matched = re.search(
        r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/(?:(?:[^/?#]+/)?status|i/web/status)/(\d+)(?:[/?#]|$)",
        url,
        flags=re.IGNORECASE,
    )
    if not matched:
        return ""
    return matched.group(1)
