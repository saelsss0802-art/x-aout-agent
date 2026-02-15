from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import (
    BudgetExceededError,
    BudgetLedger,
    ExternalPost,
    ExternalPostMetrics,
    FetchLimiter,
    RateLimiter,
    SearchLimiter,
    XClient,
    XUsage,
)
from core.db import Base, SessionLocal, engine
from core.models import (
    ActionType,
    Account,
    AccountType,
    Agent,
    AgentStatus,
    CostLog,
    DailyPDCA,
    FetchLog,
    MetricsCollectionType,
    Post,
    PostMetrics,
    PostType,
    SearchLog,
)
from .gemini_web_search_client import GeminiWebSearchClient, GeminiWebSearchError
from .summarize import GeminiSummarizeError, GeminiSummarizer
from .web_fetch_client import WebFetchClient

try:
    from .real_x_client import MissingXUserIdError, RealXClient, XApiError
except ModuleNotFoundError:
    class XApiError(RuntimeError):
        pass

    class MissingXUserIdError(XApiError):
        pass

    RealXClient = None  # type: ignore[assignment]


class WebSearchClient(Protocol):
    def search(self, query: str, k: int) -> list[dict[str, str]]: ...


class XSearchClient(Protocol):
    def search(self, query: str, k: int) -> list[dict[str, str]]: ...


class FakeWebSearchClient:
    def search(self, query: str, k: int) -> list[dict[str, str]]:
        base = [
            {
                "title": "Daily market pulse",
                "snippet": f"Summary for {query} from trusted web source.",
                "url": "https://example.com/research/market-pulse",
            },
            {
                "title": "Industry watch",
                "snippet": f"Signals and context around {query}.",
                "url": "https://example.com/research/industry-watch",
            },
        ]
        return base[:k]


class FakeXSearchClient:
    def search(self, query: str, k: int) -> list[dict[str, str]]:
        base = [
            {
                "tweet_id": "tweet-001",
                "author_id": "author-100",
                "text": f"Conversation spike about {query}",
                "created_at": "2026-01-01T09:00:00+00:00",
                "url": "https://x.com/example/status/1",
            },
            {
                "tweet_id": "tweet-002",
                "author_id": "author-101",
                "text": f"User sentiment around {query}",
                "created_at": "2026-01-01T10:00:00+00:00",
                "url": "https://x.com/example/status/2",
            },
        ]
        return base[:k]


class FakeXClient:
    def resolve_user_id(self, handle_or_me: str = "me") -> str:
        del handle_or_me
        return "fake-user-id"

    def list_posts(self, agent_id: int, target_date: date) -> list[ExternalPost]:
        base = datetime(target_date.year, target_date.month, target_date.day, 9, tzinfo=timezone.utc)
        return [
            ExternalPost(
                external_id=f"{agent_id}-{target_date.isoformat()}-001",
                posted_at=base,
                text="Daily update alpha",
                type=PostType.tweet,
            ),
            ExternalPost(
                external_id=f"{agent_id}-{target_date.isoformat()}-002",
                posted_at=base + timedelta(hours=2),
                text="Daily update beta",
                type=PostType.thread,
                media_urls=["https://example.com/image1.png"],
            ),
            ExternalPost(
                external_id=f"{agent_id}-{target_date.isoformat()}-003",
                posted_at=base + timedelta(hours=4),
                text="Daily update gamma",
                type=PostType.quote_rt,
            ),
        ]

    def get_post_metrics(self, external_post: ExternalPost) -> ExternalPostMetrics:
        seed = sum(ord(c) for c in external_post.external_id)
        likes = 10 + seed % 50
        replies = 2 + seed % 8
        retweets = 3 + seed % 12
        clicks = 15 + seed % 60
        impressions = likes * 20 + replies * 30 + retweets * 25 + clicks * 10
        return ExternalPostMetrics(
            external_id=external_post.external_id,
            impressions=impressions,
            likes=likes,
            replies=replies,
            retweets=retweets,
            clicks=clicks,
        )

    def get_daily_usage(self, usage_date: date) -> XUsage:
        return XUsage(usage_date=usage_date, units=0, raw={"source": "fake"})


def _posts_per_day(agent: Agent) -> int:
    env_value = os.getenv("POSTS_PER_DAY")
    if env_value is not None:
        try:
            return max(0, int(env_value))
        except ValueError:
            return 1
    toggles = agent.feature_toggles if isinstance(agent.feature_toggles, dict) else {}
    value = toggles.get("posts_per_day", 1)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 1


def _scheduled_datetime_for_plan(target_date: date) -> datetime:
    tz = ZoneInfo(os.getenv("WORKER_TZ", "UTC"))
    hour = int(os.getenv("POST_HOUR", "9"))
    minute = int(os.getenv("POST_MINUTE", "0"))
    next_date = target_date + timedelta(days=1)
    return datetime(next_date.year, next_date.month, next_date.day, hour, minute, tzinfo=tz)


def _generate_planned_content(agent_id: int, scheduled_at: datetime, index: int) -> str:
    return f"Daily note agent={agent_id} slot={index + 1} at {scheduled_at.isoformat()}"


def _create_next_day_posts(
    session: Session,
    *,
    agent: Agent,
    target_date: date,
    pdca: DailyPDCA,
    ledger: BudgetLedger,
) -> list[dict[str, object]]:
    planned_count = _posts_per_day(agent)
    if planned_count <= 0:
        return []

    scheduled_at = _scheduled_datetime_for_plan(target_date)
    scheduled_start = datetime(scheduled_at.year, scheduled_at.month, scheduled_at.day, 0, 0, tzinfo=scheduled_at.tzinfo)
    scheduled_end = scheduled_start + timedelta(days=1)
    existing = session.scalars(
        select(Post).where(
            Post.agent_id == agent.id,
            Post.scheduled_at.is_not(None),
            Post.scheduled_at >= scheduled_start,
            Post.scheduled_at < scheduled_end,
            Post.posted_at.is_(None),
        )
    ).all()
    missing = max(0, planned_count - len(existing))

    created: list[dict[str, object]] = []
    for idx in range(missing):
        ledger.reserve(x_cost=Decimal("0.00"), llm_cost=Decimal("0.50"))
        content = _generate_planned_content(agent.id, scheduled_at, len(existing) + idx)
        post = Post(
            agent_id=agent.id,
            content=content,
            type=PostType.tweet,
            media_urls=[],
            scheduled_at=scheduled_at + timedelta(minutes=5 * (len(existing) + idx)),
            posted_at=None,
        )
        session.add(post)
        session.flush()
        created.append({"id": post.id, "scheduled_at": post.scheduled_at.isoformat(), "type": post.type.value})

    posts_created = list(pdca.posts_created or [])
    posts_created.extend(created)
    pdca.posts_created = posts_created
    return created


def _build_x_client(account: Account | None = None) -> XClient:
    if os.getenv("USE_REAL_X") == "1":
        token = os.getenv("X_BEARER_TOKEN")
        if not token:
            raise XApiError("X_BEARER_TOKEN is required when USE_REAL_X=1")
        account_user_id = None
        if account and isinstance(account.api_keys, dict):
            raw = account.api_keys.get("x_user_id")
            if isinstance(raw, str):
                account_user_id = raw
        user_id = os.getenv("X_USER_ID") or account_user_id
        if RealXClient is None:
            raise XApiError("httpx is required when USE_REAL_X=1")
        return RealXClient(bearer_token=token, user_id=user_id)
    return FakeXClient()


def _ensure_agent(session: Session, agent_id: int) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent:
        return agent

    account = Account(
        name=f"agent-{agent_id}",
        type=AccountType.business,
        api_keys={"x": "fake"},
        media_assets_path="/tmp",
    )
    session.add(account)
    session.flush()

    agent = Agent(id=agent_id, account_id=account.id, status=AgentStatus.active, feature_toggles={})
    session.add(agent)
    session.flush()
    return agent


def _upsert_post(session: Session, agent_id: int, external_post: ExternalPost) -> Post:
    existing = session.scalar(
        select(Post).where(Post.agent_id == agent_id, Post.external_id == external_post.external_id)
    )
    if existing:
        existing.content = external_post.text
        existing.posted_at = external_post.posted_at
        existing.type = external_post.type
        existing.media_urls = external_post.media_urls
        return existing

    post = Post(
        agent_id=agent_id,
        external_id=external_post.external_id,
        content=external_post.text,
        posted_at=external_post.posted_at,
        type=external_post.type,
        media_urls=external_post.media_urls,
    )
    session.add(post)
    session.flush()
    return post


def _save_confirmed_metrics(
    session: Session,
    post: Post,
    metrics: ExternalPostMetrics,
    collected_at: datetime,
) -> bool:
    exists = session.scalar(
        select(PostMetrics).where(
            PostMetrics.post_id == post.id,
            PostMetrics.collection_type == MetricsCollectionType.confirmed,
        )
    )
    if exists:
        return False

    session.add(
        PostMetrics(
            post_id=post.id,
            collection_type=MetricsCollectionType.confirmed,
            collected_at=collected_at,
            impressions=metrics.impressions,
            likes=metrics.likes,
            replies=metrics.replies,
            retweets=metrics.retweets,
            clicks=metrics.clicks,
            engagements=metrics.likes + metrics.replies + metrics.retweets + metrics.clicks,
        )
    )
    return True


def _apply_usage(session: Session, *, agent_id: int, usage_date: date, x_client: XClient) -> bool:
    if os.getenv("USE_X_USAGE") != "1":
        return True

    usage = x_client.get_daily_usage(usage_date=usage_date)
    cost_log = session.scalar(select(CostLog).where(CostLog.agent_id == agent_id, CostLog.date == usage_date))
    if cost_log is None:
        cost_log = CostLog(
            agent_id=agent_id,
            date=usage_date,
            x_api_cost=Decimal("0"),
            llm_cost=Decimal("0"),
            image_gen_cost=Decimal("0"),
            total=Decimal("0"),
        )
        session.add(cost_log)
    cost_log.x_usage_units = usage.units
    cost_log.x_usage_raw = usage.raw

    unit_price = Decimal(os.getenv("X_UNIT_PRICE", "0"))
    if unit_price > 0:
        measured_x_cost = Decimal(usage.units) * unit_price
        cost_log.x_api_cost = measured_x_cost.quantize(Decimal("0.01"))
        cost_log.total = Decimal(cost_log.x_api_cost) + Decimal(cost_log.llm_cost) + Decimal(cost_log.image_gen_cost)
    return True




def _build_research_queries(agent_id: int, target_date: date) -> list[str]:
    topic = os.getenv("SEARCH_TOPIC", f"agent-{agent_id}-insights")
    return [f"{topic} {target_date.isoformat()}"]


def _record_search(
    session: Session,
    *,
    agent_id: int,
    target_date: date,
    source: str,
    query: str,
    results: dict[str, object],
    cost_estimate: Decimal,
) -> None:
    session.add(
        SearchLog(
            agent_id=agent_id,
            date=target_date,
            source=source,
            query=query,
            results_json=results,
            cost_estimate=cost_estimate,
        )
    )


def _normalize_search_log_payload(raw_results: object, *, k: int, snippet_limit: int) -> dict[str, object]:
    top_k = max(1, min(k, 5))
    payload: dict[str, object] = {"results": [], "citations": [], "notes": {"grounded": False}}

    if isinstance(raw_results, dict):
        items = raw_results.get("results") if isinstance(raw_results.get("results"), list) else []
        citations_raw = raw_results.get("citations") if isinstance(raw_results.get("citations"), list) else []
        notes_raw = raw_results.get("notes") if isinstance(raw_results.get("notes"), dict) else {}
    elif isinstance(raw_results, list):
        items = raw_results
        citations_raw = []
        notes_raw = {}
    else:
        items = []
        citations_raw = []
        notes_raw = {}

    normalized_results: list[dict[str, str]] = []
    for item in items[:top_k]:
        if not isinstance(item, dict):
            continue
        normalized_results.append(
            {
                "title": str(item.get("title", "")),
                "snippet": str(item.get("snippet", ""))[:snippet_limit],
                "url": str(item.get("url", "")),
            }
        )

    citations: list[dict[str, str]] = []
    for item in citations_raw:
        if not isinstance(item, dict):
            continue
        citations.append({"url": str(item.get("url", "")), "title": str(item.get("title", ""))})

    payload["results"] = normalized_results
    payload["citations"] = citations
    payload["notes"] = {"grounded": bool(notes_raw.get("grounded", False))}
    return payload


def _run_daily_research(
    session: Session,
    *,
    agent_id: int,
    target_date: date,
    ledger: BudgetLedger,
    pdca: DailyPDCA,
    web_client: WebSearchClient,
    x_search_client: XSearchClient,
) -> dict[str, object]:
    limiter = SearchLimiter(
        session,
        agent_id=agent_id,
        target_date=target_date,
        x_search_max=int(os.getenv("X_SEARCH_MAX", "10")),
        web_search_max=int(os.getenv("WEB_SEARCH_MAX", "10")),
    )
    k = int(os.getenv("SEARCH_TOP_K", "3"))
    x_search_cost = Decimal(os.getenv("X_SEARCH_COST", "1.00"))
    web_search_cost = Decimal(os.getenv("WEB_SEARCH_COST", os.getenv("GEMINI_GROUNDING_UNIT_COST", "1.00")))
    search_snippet_limit = int(os.getenv("SEARCH_SNIPPET_LIMIT", "300"))

    records: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for query in _build_research_queries(agent_id, target_date):
        if limiter.is_limited(source="x"):
            skipped.append({"source": "x", "query": query, "reason": "search_rate_limited"})
        else:
            try:
                ledger.reserve(x_cost=x_search_cost, llm_cost=Decimal("0"))
                x_results = x_search_client.search(query, k)
                normalized = [
                    {
                        "title": item.get("text", ""),
                        "snippet": item.get("text", ""),
                        "url": item.get("url", ""),
                    }
                    for item in x_results
                ]
                _record_search(
                    session,
                    agent_id=agent_id,
                    target_date=target_date,
                    source="x",
                    query=query,
                    results=_normalize_search_log_payload(normalized, k=k, snippet_limit=search_snippet_limit),
                    cost_estimate=x_search_cost,
                )
                records.append({"source": "x", "query": query, "results": normalized})
            except BudgetExceededError:
                skipped.append({"source": "x", "query": query, "reason": "search_budget_exceeded"})

        if limiter.is_limited(source="web"):
            skipped.append({"source": "web", "query": query, "reason": "search_rate_limited"})
        else:
            try:
                # Web search is tracked inside the LLM budget bucket for unified daily control.
                ledger.reserve(x_cost=Decimal("0"), llm_cost=web_search_cost)
                web_results = web_client.search(query, k)
                raw_payload = (
                    web_client.last_payload
                    if hasattr(web_client, "last_payload") and isinstance(web_client.last_payload, dict)
                    else web_results
                )
                normalized_payload = _normalize_search_log_payload(raw_payload, k=k, snippet_limit=search_snippet_limit)
                _record_search(
                    session,
                    agent_id=agent_id,
                    target_date=target_date,
                    source="web",
                    query=query,
                    results=normalized_payload,
                    cost_estimate=web_search_cost,
                )
                ledger.commit()
                records.append({"source": "web", "query": query, "results": web_results})
            except BudgetExceededError:
                skipped.append({"source": "web", "query": query, "reason": "search_budget_exceeded"})
            except (GeminiWebSearchError, ValueError, RuntimeError):
                skipped.append({"source": "web", "query": query, "reason": "gemini_search_failed"})

    analytics = dict(pdca.analytics_summary or {})
    analytics["search"] = {
        "count": len(records),
        "last_queries": [item["query"] for item in records[-3:]],
        "skipped": skipped,
        "usage": {
            "web_search_provider": "gemini" if os.getenv("USE_GEMINI_WEB_SEARCH") == "1" else "fake",
            "web_search_status": "ok" if not any(item["reason"] == "gemini_search_failed" for item in skipped) else "failed",
        },
    }
    pdca.analytics_summary = analytics
    return {"records": records, "skipped": skipped}



def _query_needs_fetch(query: str) -> bool:
    keywords = ("方法", "手順", "比較", "料金", "変更")
    return any(keyword in query for keyword in keywords)


def _snippet_is_ambiguous(snippet: str) -> bool:
    cleaned = snippet.strip()
    if len(cleaned) < 60:
        return True
    return "..." in cleaned or "詳細" in cleaned


def _run_fetch_and_summary(
    session: Session,
    *,
    agent_id: int,
    target_date: date,
    ledger: BudgetLedger,
    search_records: list[dict[str, object]],
) -> dict[str, object]:
    fetch_limiter = FetchLimiter(
        session,
        agent_id=agent_id,
        target_date=target_date,
        web_fetch_max=int(os.getenv("WEB_FETCH_MAX", "3")),
    )
    fetch_cost = Decimal(os.getenv("WEB_FETCH_LLM_COST", "0.30"))
    summarize_cost = Decimal(os.getenv("WEB_SUMMARIZE_LLM_COST", "1.00"))
    fetch_client = WebFetchClient()

    summarize_enabled = os.getenv("USE_GEMINI_SUMMARIZE", "1") == "1"
    summarizer: GeminiSummarizer | None = None
    if summarize_enabled:
        try:
            summarizer = GeminiSummarizer()
        except GeminiSummarizeError:
            summarizer = None

    processed = 0
    summarized = 0
    failed = 0
    skipped: list[dict[str, str]] = []
    logs: list[dict[str, object]] = []

    for record in search_records:
        if record.get("source") != "web":
            continue
        query = str(record.get("query", ""))
        results = record.get("results")
        if not isinstance(results, list):
            continue
        need_fetch = _query_needs_fetch(query)
        if not need_fetch and any(isinstance(item, dict) and _snippet_is_ambiguous(str(item.get("snippet", ""))) for item in results):
            need_fetch = True
        if not need_fetch:
            continue

        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if not url:
                continue

            if fetch_limiter.is_limited():
                skipped.append({"url": url, "reason": "fetch_limit_reached"})
                session.add(
                    FetchLog(
                        agent_id=agent_id,
                        date=target_date,
                        url=url,
                        status="skipped",
                        failure_reason="fetch_limit_reached",
                        cost_estimate=Decimal("0"),
                    )
                )
                break

            try:
                ledger.reserve(x_cost=Decimal("0"), llm_cost=fetch_cost)
            except BudgetExceededError:
                skipped.append({"url": url, "reason": "fetch_budget_exceeded"})
                session.add(
                    FetchLog(
                        agent_id=agent_id,
                        date=target_date,
                        url=url,
                        status="skipped",
                        failure_reason="fetch_budget_exceeded",
                        cost_estimate=Decimal("0"),
                    )
                )
                break

            fetch_result = fetch_client.fetch(url)
            summary_payload: dict[str, object] | None = None
            status = fetch_result.status
            reason = fetch_result.failure_reason
            cost_estimate = fetch_cost

            if fetch_result.status == "succeeded" and fetch_result.extracted_text and summarizer is not None:
                try:
                    ledger.reserve(x_cost=Decimal("0"), llm_cost=summarize_cost)
                    summary_payload = summarizer.summarize(fetch_result.extracted_text)
                    summarized += 1
                    cost_estimate += summarize_cost
                except (GeminiSummarizeError, ValueError, RuntimeError, BudgetExceededError) as exc:
                    status = "failed"
                    reason = f"summarize_failed:{exc}"

            if status == "failed":
                failed += 1

            session.add(
                FetchLog(
                    agent_id=agent_id,
                    date=target_date,
                    url=fetch_result.url,
                    status=status,
                    http_status=fetch_result.http_status,
                    content_type=fetch_result.content_type,
                    content_length=fetch_result.content_length,
                    extracted_text=fetch_result.extracted_text,
                    summary_json=summary_payload,
                    failure_reason=reason,
                    cost_estimate=cost_estimate if status == "succeeded" else Decimal("0"),
                )
            )
            logs.append(
                {
                    "url": fetch_result.url,
                    "status": status,
                    "http_status": fetch_result.http_status,
                    "summary_safe_to_use": summary_payload.get("safe_to_use") if isinstance(summary_payload, dict) else None,
                    "reason": reason,
                }
            )
            processed += 1
            break

    return {
        "fetch_count": processed,
        "summarize_count": summarized,
        "failed_count": failed,
        "skipped": skipped,
        "logs": logs,
    }


def _write_daily_log(agent_id: int, target_date: date, payload: dict[str, object]) -> Path:
    log_dir = Path("apps/worker/logs") / str(agent_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{target_date.isoformat()}.json"
    log_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return log_path


def run_daily_routine(agent_id: int, base_date: date, x_client: XClient | None = None) -> dict[str, object]:
    target_date = base_date - timedelta(days=2)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        agent = _ensure_agent(session, agent_id)
        x_client = x_client or _build_x_client(agent.account)
        if os.getenv("USE_GEMINI_WEB_SEARCH") == "1":
            web_search_client = GeminiWebSearchClient()
        else:
            web_search_client = FakeWebSearchClient()
        x_search_client: XSearchClient = FakeXSearchClient()
        x_cost = Decimal("1.00")
        llm_cost = Decimal("2.00")
        ledger = BudgetLedger(
            session,
            agent_id=agent_id,
            target_date=target_date,
            daily_budget=agent.daily_budget,
            split_x=agent.budget_split_x,
            split_llm=agent.budget_split_llm,
        )
        rate_limiter = RateLimiter(session, agent_id=agent_id, target_date=target_date, daily_total_limit=3)

        if rate_limiter.is_limited(action_type=ActionType.reply, requested=1):
            rate_status = rate_limiter.status(action_type=ActionType.reply)
            budget_status = ledger.status()
            pdca = session.scalar(
                select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date)
            )
            if pdca is None:
                pdca = DailyPDCA(
                    agent_id=agent_id,
                    date=target_date,
                    analytics_summary={"status": "skip", "reason": "rate_limited"},
                    analysis={"status": "skipped", "reason": "rate_limited"},
                    strategy={"next_action": "wait"},
                    posts_created=[],
                )
                session.add(pdca)
            session.commit()
            return {
                "target_date": target_date,
                "log_path": None,
                "posts": 0,
                "status": "skip",
                "reason": "rate_limited",
                "budget_status": {"total_spent": str(budget_status.total_spent), "daily_limit": str(budget_status.daily_limit)},
                "rate_status": rate_status,
            }

        try:
            ledger.reserve(x_cost=x_cost, llm_cost=llm_cost)
        except BudgetExceededError:
            rate_status = rate_limiter.status(action_type=ActionType.reply)
            budget_status = ledger.status()
            pdca = session.scalar(
                select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date)
            )
            if pdca is None:
                pdca = DailyPDCA(
                    agent_id=agent_id,
                    date=target_date,
                    analytics_summary={"status": "skip", "reason": "budget_exceeded"},
                    analysis={"status": "skipped", "reason": "budget_exceeded"},
                    strategy={"next_action": "wait"},
                    posts_created=[],
                )
                session.add(pdca)
            session.commit()
            return {
                "target_date": target_date,
                "log_path": None,
                "posts": 0,
                "status": "skip",
                "reason": "budget_exceeded",
                "budget_status": {
                    "total_spent": str(budget_status.total_spent),
                    "daily_limit": str(budget_status.daily_limit),
                    "reserved_total": str(budget_status.total_reserved),
                },
                "rate_status": rate_status,
            }

        try:
            external_posts = x_client.list_posts(agent_id=agent_id, target_date=target_date)
        except MissingXUserIdError as exc:
            rate_status = rate_limiter.status(action_type=ActionType.reply)
            budget_status = ledger.status()
            pdca = session.scalar(
                select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date)
            )
            if pdca is None:
                pdca = DailyPDCA(
                    agent_id=agent_id,
                    date=target_date,
                    analytics_summary={"status": "skip", "reason": "missing_user_id", "message": str(exc)},
                    analysis={"status": "skipped", "reason": "missing_user_id"},
                    strategy={"next_action": "set_x_user_id"},
                    posts_created=[],
                )
                session.add(pdca)
            else:
                pdca.analytics_summary = {"status": "skip", "reason": "missing_user_id", "message": str(exc)}
                pdca.analysis = {"status": "skipped", "reason": "missing_user_id"}
                pdca.strategy = {"next_action": "set_x_user_id"}
                pdca.posts_created = []
            session.commit()
            log_path = _write_daily_log(
                agent_id,
                target_date,
                {
                    "agent_id": agent_id,
                    "base_date": base_date.isoformat(),
                    "target_date": target_date.isoformat(),
                    "status": "skip",
                    "reason": "missing_user_id",
                    "message": str(exc),
                },
            )
            return {
                "target_date": target_date,
                "log_path": log_path,
                "posts": 0,
                "status": "skip",
                "reason": "missing_user_id",
                "budget_status": {"total_spent": str(budget_status.total_spent), "daily_limit": str(budget_status.daily_limit)},
                "rate_status": rate_status,
            }

        inserted_metrics = 0
        metric_rows: list[dict[str, object]] = []
        post_ids: list[int] = []
        impressions_unavailable = False

        for external_post in external_posts:
            post = _upsert_post(session, agent_id, external_post)
            post_ids.append(post.id)
            external_metrics = x_client.get_post_metrics(external_post)
            if external_metrics.impressions_unavailable:
                impressions_unavailable = True
            created = _save_confirmed_metrics(session, post, external_metrics, now)
            if created:
                inserted_metrics += 1
            metric_rows.append(asdict(external_metrics))

        pdca = session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date))
        analytics_summary = {
            "target_date": target_date.isoformat(),
            "post_count": len(external_posts),
            "confirmed_metrics_created": inserted_metrics,
            "impressions_unavailable": impressions_unavailable,
            "search": {"count": 0, "last_queries": [], "skipped": []},
        }
        if pdca is None:
            pdca = DailyPDCA(
                agent_id=agent_id,
                date=target_date,
                analytics_summary=analytics_summary,
                analysis={"status": "completed"},
                strategy={"next_action": "continue"},
                posts_created=[{"external_id": p.external_id} for p in external_posts],
            )
            session.add(pdca)
        else:
            pdca.analytics_summary = analytics_summary

        research_summary = _run_daily_research(
            session,
            agent_id=agent_id,
            target_date=target_date,
            ledger=ledger,
            pdca=pdca,
            web_client=web_search_client,
            x_search_client=x_search_client,
        )
        fetch_summary = _run_fetch_and_summary(
            session,
            agent_id=agent_id,
            target_date=target_date,
            ledger=ledger,
            search_records=research_summary["records"],
        )
        analytics = dict(pdca.analytics_summary or {})
        analytics["fetch"] = {
            "fetch_count": fetch_summary["fetch_count"],
            "summarize_count": fetch_summary["summarize_count"],
            "failed_count": fetch_summary["failed_count"],
            "skipped": fetch_summary["skipped"],
        }
        pdca.analytics_summary = analytics

        planned_posts = _create_next_day_posts(
            session,
            agent=agent,
            target_date=target_date,
            pdca=pdca,
            ledger=ledger,
        )

        ledger.commit()

        usage_fetch_failed = False
        try:
            _apply_usage(session, agent_id=agent_id, usage_date=target_date, x_client=x_client)
        except (XApiError, ValueError):
            usage_fetch_failed = True

        if usage_fetch_failed:
            pdca.analysis = {**pdca.analysis, "usage_fetch_failed": True}

        budget_status = ledger.status()
        rate_status = rate_limiter.status(action_type=ActionType.reply)

        session.commit()

    log_payload = {
        "agent_id": agent_id,
        "base_date": base_date.isoformat(),
        "target_date": target_date.isoformat(),
        "status": "success",
        "posts": post_ids,
        "metrics": metric_rows,
        "confirmed_metrics_created": inserted_metrics,
        "cost": {"x_api_cost": str(x_cost), "llm_cost": str(llm_cost), "total": str(x_cost + llm_cost)},
        "planned_posts": planned_posts,
        "research": research_summary,
        "fetch": fetch_summary,
    }
    log_path = _write_daily_log(agent_id, target_date, log_payload)

    return {
        "target_date": target_date,
        "log_path": log_path,
        "posts": len(post_ids),
        "status": "success",
        "budget_status": {
            "total_spent": str(budget_status.total_spent),
            "daily_limit": str(budget_status.daily_limit),
        },
        "rate_status": rate_status,
    }
