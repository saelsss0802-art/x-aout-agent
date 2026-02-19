from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import BudgetLedger
from core.models import FetchLog, PostType, SearchLog, TargetPostCandidate

_URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class PostDraft:
    type: PostType
    text: str
    thread_parts: list[str] | None = None
    target_post_url: str | None = None
    allow_url: bool = False


@dataclass(frozen=True)
class PlanBuildResult:
    drafts: list[PostDraft]
    used_search_material: bool


def _clean_text(text: str) -> str:
    return " ".join(text.split())[:240]


def _strip_urls(text: str) -> str:
    return _URL_RE.sub("", text).strip()


def _extract_web_facts(search_logs: list[SearchLog], fetch_logs: list[FetchLog]) -> list[str]:
    facts: list[str] = []
    for log in search_logs:
        payload = log.results_json if isinstance(log.results_json, dict) else {}
        results = payload.get("results", []) if isinstance(payload, dict) else []
        for item in results if isinstance(results, list) else []:
            if isinstance(item, dict):
                snippet = _clean_text(str(item.get("snippet", "")))
                if snippet:
                    facts.append(_strip_urls(snippet))

    for log in fetch_logs:
        if isinstance(log.summary_json, dict):
            summary = _clean_text(str(log.summary_json.get("summary", "")))
            if summary:
                facts.append(_strip_urls(summary))
        elif log.extracted_text:
            facts.append(_strip_urls(_clean_text(log.extracted_text)))

    return [fact for fact in facts if fact]


def _extract_x_targets(session: Session, *, agent_id: int, target_date: date) -> list[str]:
    rows = session.scalars(
        select(TargetPostCandidate)
        .where(
            TargetPostCandidate.agent_id == agent_id,
            TargetPostCandidate.date == target_date,
            TargetPostCandidate.used.is_(False),
        )
        .order_by(TargetPostCandidate.post_created_at.asc(), TargetPostCandidate.id.asc())
    ).all()
    return [row.url for row in rows]


def _append_optional_url(text: str, target_url: str | None, allow_url: bool) -> str:
    clean = _strip_urls(text)
    if allow_url and target_url:
        return f"{clean} {target_url}".strip()
    return clean


def _fallback_facts(agent_id: int, target_date: date) -> list[str]:
    return [
        f"Agent {agent_id} focus for {target_date.isoformat()}",
        "One useful lesson from recent work and a practical next step",
        "A short observation plus a concrete action for tomorrow",
    ]


def build_post_drafts(
    session: Session,
    *,
    agent_id: int,
    target_date: date,
    posts_per_day: int,
    ledger: BudgetLedger,
) -> PlanBuildResult:
    if posts_per_day <= 0:
        return PlanBuildResult(drafts=[], used_search_material=False)

    plan_cost = Decimal(os.getenv("PLAN_LLM_COST", "0.50"))
    ledger.reserve(x_cost=Decimal("0"), llm_cost=plan_cost)

    search_logs = session.scalars(
        select(SearchLog).where(SearchLog.agent_id == agent_id, SearchLog.date == target_date).order_by(SearchLog.id.asc())
    ).all()
    fetch_logs = session.scalars(
        select(FetchLog).where(FetchLog.agent_id == agent_id, FetchLog.date == target_date).order_by(FetchLog.id.asc())
    ).all()

    facts = _extract_web_facts(search_logs, fetch_logs)
    targets = _extract_x_targets(session, agent_id=agent_id, target_date=target_date)
    used_search_material = bool(facts or targets)
    if not facts:
        facts = _fallback_facts(agent_id, target_date)

    thread_ratio = max(0.0, float(os.getenv("PLAN_THREAD_RATIO", "0.2")))
    reply_ratio = max(0.0, float(os.getenv("PLAN_REPLY_RATIO", "0.2")))
    quote_ratio = max(0.0, float(os.getenv("PLAN_QUOTE_RATIO", "0.2")))

    thread_count = min(posts_per_day, int(posts_per_day * thread_ratio))
    reply_count = min(posts_per_day - thread_count, int(posts_per_day * reply_ratio))
    quote_count = min(posts_per_day - thread_count - reply_count, int(posts_per_day * quote_ratio))

    # reply/quote need target urls; if none, re-distribute to tweet/thread only.
    if not targets:
        thread_extra = reply_count + quote_count
        reply_count = 0
        quote_count = 0
        thread_count = min(posts_per_day, thread_count + thread_extra)

    if reply_count + quote_count > 3:
        overflow = reply_count + quote_count - 3
        while overflow > 0 and quote_count > 0:
            quote_count -= 1
            overflow -= 1
        while overflow > 0 and reply_count > 0:
            reply_count -= 1
            overflow -= 1

    tweet_count = max(0, posts_per_day - thread_count - reply_count - quote_count)

    drafts: list[PostDraft] = []
    allow_validation_url = os.getenv("PLAN_ALLOW_URL_FOR_VALIDATION", "0") == "1"

    for idx in range(tweet_count):
        text = _append_optional_url(f"Insight: {facts[idx % len(facts)]}", None, False)
        drafts.append(PostDraft(type=PostType.tweet, text=text, allow_url=False))

    for idx in range(thread_count):
        base = facts[(tweet_count + idx) % len(facts)]
        parts = [
            _strip_urls(f"Thread {idx + 1}/2: {base}"),
            _strip_urls(f"Thread {idx + 1}/2 action: verify impact and report observations."),
        ]
        drafts.append(PostDraft(type=PostType.thread, text=parts[0], thread_parts=parts, allow_url=False))

    for idx in range(reply_count):
        target = targets[idx % len(targets)]
        text = _append_optional_url(
            "Thanks for the perspective. One practical point is to test assumptions.",
            target,
            allow_validation_url,
        )
        drafts.append(
            PostDraft(
                type=PostType.reply,
                text=text,
                target_post_url=target,
                allow_url=allow_validation_url,
            )
        )

    for idx in range(quote_count):
        target = targets[(reply_count + idx) % len(targets)]
        text = _append_optional_url(
            "Useful context. We should compare with recent outcomes before scaling.",
            target,
            allow_validation_url,
        )
        drafts.append(
            PostDraft(
                type=PostType.quote_rt,
                text=text,
                target_post_url=target,
                allow_url=allow_validation_url,
            )
        )

    return PlanBuildResult(drafts=drafts, used_search_material=used_search_material)
