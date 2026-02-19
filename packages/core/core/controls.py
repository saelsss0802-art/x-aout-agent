from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ActionType, Agent, AgentStatus, AuditLog, CostLog, DailyPDCA, EngagementAction, FetchLog, SearchLog


def normalize_post_content(content: str, thread_parts: list[str] | None = None) -> str:
    if thread_parts:
        base = "\n".join(part.strip() for part in thread_parts if isinstance(part, str) and part.strip())
    else:
        base = content
    normalized = " ".join(base.split()).strip().lower()
    return normalized


def build_post_content_hash(content: str, thread_parts: list[str] | None = None) -> str:
    normalized = normalize_post_content(content, thread_parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class BudgetExceededError(RuntimeError):
    pass


@dataclass
class BudgetStatus:
    total_spent: Decimal
    x_spent: Decimal
    llm_spent: Decimal
    total_reserved: Decimal
    x_reserved: Decimal
    llm_reserved: Decimal
    daily_limit: Decimal
    x_limit: Decimal
    llm_limit: Decimal


class BudgetLedger:
    def __init__(
        self,
        session: Session,
        *,
        agent_id: int,
        target_date: date,
        daily_budget: int,
        split_x: int,
        split_llm: int,
    ) -> None:
        self.session = session
        self.agent_id = agent_id
        self.target_date = target_date
        self.daily_limit = Decimal(daily_budget)
        self.x_limit = Decimal(split_x)
        self.llm_limit = Decimal(split_llm)
        self._x_reserved = Decimal("0")
        self._llm_reserved = Decimal("0")

    def _spent(self) -> tuple[Decimal, Decimal, Decimal]:
        x_spent, llm_spent, total_spent = self.session.execute(
            select(
                func.coalesce(func.sum(CostLog.x_api_cost), Decimal("0")),
                func.coalesce(func.sum(CostLog.llm_cost), Decimal("0")),
                func.coalesce(func.sum(CostLog.total), Decimal("0")),
            ).where(CostLog.agent_id == self.agent_id, CostLog.date == self.target_date)
        ).one()
        return Decimal(x_spent), Decimal(llm_spent), Decimal(total_spent)

    def reserve(self, *, x_cost: Decimal, llm_cost: Decimal) -> None:
        x_spent, llm_spent, total_spent = self._spent()
        next_x = x_spent + self._x_reserved + x_cost
        next_llm = llm_spent + self._llm_reserved + llm_cost
        next_total = total_spent + self._x_reserved + self._llm_reserved + x_cost + llm_cost

        if next_x > self.x_limit or next_llm > self.llm_limit or next_total > self.daily_limit:
            raise BudgetExceededError("Daily budget exceeded")

        self._x_reserved += x_cost
        self._llm_reserved += llm_cost

    def commit(self) -> None:
        if self._x_reserved == Decimal("0") and self._llm_reserved == Decimal("0"):
            return

        cost = self.session.scalar(
            select(CostLog).where(CostLog.agent_id == self.agent_id, CostLog.date == self.target_date)
        )
        total_reserved = self._x_reserved + self._llm_reserved
        if cost is None:
            self.session.add(
                CostLog(
                    agent_id=self.agent_id,
                    date=self.target_date,
                    x_api_cost=self._x_reserved,
                    x_api_cost_estimate=self._x_reserved,
                    llm_cost=self._llm_reserved,
                    image_gen_cost=Decimal("0"),
                    total=total_reserved,
                )
            )
        else:
            cost.x_api_cost = Decimal(cost.x_api_cost) + self._x_reserved
            cost.x_api_cost_estimate = Decimal(cost.x_api_cost_estimate) + self._x_reserved
            cost.llm_cost = Decimal(cost.llm_cost) + self._llm_reserved
            cost.total = Decimal(cost.total) + total_reserved

        self._x_reserved = Decimal("0")
        self._llm_reserved = Decimal("0")

    def status(self) -> BudgetStatus:
        x_spent, llm_spent, total_spent = self._spent()
        return BudgetStatus(
            total_spent=total_spent,
            x_spent=x_spent,
            llm_spent=llm_spent,
            total_reserved=self._x_reserved + self._llm_reserved,
            x_reserved=self._x_reserved,
            llm_reserved=self._llm_reserved,
            daily_limit=self.daily_limit,
            x_limit=self.x_limit,
            llm_limit=self.llm_limit,
        )


class SearchLimiter:
    def __init__(
        self,
        session: Session,
        *,
        agent_id: int,
        target_date: date,
        x_search_max: int = 10,
        web_search_max: int = 10,
    ) -> None:
        self.session = session
        self.agent_id = agent_id
        self.target_date = target_date
        self.x_search_max = x_search_max
        self.web_search_max = web_search_max

    def _count(self, *, source: str) -> int:
        return int(
            self.session.scalar(
                select(func.count(SearchLog.id)).where(
                    SearchLog.agent_id == self.agent_id,
                    SearchLog.date == self.target_date,
                    SearchLog.source == source,
                )
            )
            or 0
        )

    def is_limited(self, *, source: str, requested: int = 1) -> bool:
        source_max = self.x_search_max if source == "x" else self.web_search_max
        return self._count(source=source) + requested > source_max

    def status(self, *, source: str) -> dict[str, int | str]:
        source_max = self.x_search_max if source == "x" else self.web_search_max
        used = self._count(source=source)
        return {
            "source": source,
            "daily_limit": source_max,
            "used": used,
            "remaining": max(0, source_max - used),
        }


class FetchLimiter:
    def __init__(self, session: Session, *, agent_id: int, target_date: date, web_fetch_max: int = 3) -> None:
        self.session = session
        self.agent_id = agent_id
        self.target_date = target_date
        self.web_fetch_max = web_fetch_max

    def _count(self) -> int:
        return int(
            self.session.scalar(
                select(func.count(FetchLog.id)).where(
                    FetchLog.agent_id == self.agent_id,
                    FetchLog.date == self.target_date,
                    FetchLog.status.in_(["succeeded", "failed"]),
                )
            )
            or 0
        )

    def is_limited(self, *, requested: int = 1) -> bool:
        return self._count() + requested > self.web_fetch_max

    def status(self) -> dict[str, int | str]:
        used = self._count()
        return {
            "source": "web_fetch",
            "daily_limit": self.web_fetch_max,
            "used": used,
            "remaining": max(0, self.web_fetch_max - used),
        }


class RateLimiter:
    def __init__(self, session: Session, *, agent_id: int, target_date: date, daily_total_limit: int = 3) -> None:
        self.session = session
        self.agent_id = agent_id
        self.target_date = target_date
        self.daily_total_limit = daily_total_limit

    def _count_total(self) -> int:
        return int(
            self.session.scalar(
                select(func.count(EngagementAction.id)).where(
                    EngagementAction.agent_id == self.agent_id,
                    func.date(EngagementAction.executed_at) == self.target_date.isoformat(),
                )
            )
            or 0
        )

    def _count_by_type(self, action_type: ActionType) -> int:
        return int(
            self.session.scalar(
                select(func.count(EngagementAction.id)).where(
                    EngagementAction.agent_id == self.agent_id,
                    EngagementAction.action_type == action_type,
                    func.date(EngagementAction.executed_at) == self.target_date.isoformat(),
                )
            )
            or 0
        )

    def is_limited(self, *, action_type: ActionType, requested: int = 1) -> bool:
        _ = self._count_by_type(action_type)
        return self._count_total() + requested > self.daily_total_limit

    def status(self, *, action_type: ActionType) -> dict[str, int | str]:
        used_total = self._count_total()
        used_type = self._count_by_type(action_type)
        return {
            "action_type": action_type.value,
            "daily_total_limit": self.daily_total_limit,
            "total_used": used_total,
            "total_remaining": max(0, self.daily_total_limit - used_total),
            "type_used": used_type,
        }


class UsageReconciler:
    def __init__(self, session: Session, *, app_agent_id: int = 0, unit_price: Decimal | None = None) -> None:
        self.session = session
        self.app_agent_id = app_agent_id
        self.unit_price = unit_price

    def reconcile_x_usage(self, *, target_date: date, units: int, raw: dict[str, object]) -> CostLog:
        cost_log = self.session.scalar(
            select(CostLog).where(CostLog.agent_id == self.app_agent_id, CostLog.date == target_date)
        )
        if cost_log is None:
            cost_log = CostLog(
                agent_id=self.app_agent_id,
                date=target_date,
                x_api_cost=Decimal("0"),
                x_api_cost_estimate=Decimal("0"),
                llm_cost=Decimal("0"),
                image_gen_cost=Decimal("0"),
                total=Decimal("0"),
            )
            self.session.add(cost_log)

        cost_log.x_usage_units = units
        cost_log.x_usage_raw = raw

        if self.unit_price is not None and self.unit_price > Decimal("0"):
            cost_log.x_api_cost_actual = (Decimal(units) * self.unit_price).quantize(Decimal("0.01"))
        else:
            cost_log.x_api_cost_actual = None

        return cost_log


class GuardManager:
    def __init__(self, session: Session) -> None:
        self.session = session

    def is_agent_runnable(self, agent: Agent, now: datetime) -> bool:
        if agent.status != AgentStatus.active:
            return False
        if agent.stop_until is None:
            return True
        stop_until = agent.stop_until
        if stop_until.tzinfo is None:
            stop_until = stop_until.replace(tzinfo=timezone.utc)
        return stop_until <= now

    def record_audit(
        self,
        *,
        agent_id: int,
        target_date: date,
        source: str,
        event_type: str,
        status: str,
        reason: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> AuditLog:
        audit = AuditLog(
            agent_id=agent_id,
            date=target_date,
            source=source,
            event_type=event_type,
            status=status,
            reason=reason,
            payload_json=payload or {},
        )
        self.session.add(audit)
        return audit

    def maybe_auto_stop(self, agent_id: int, *, now: datetime, reason: str, source: str, payload: dict[str, object] | None = None) -> Agent | None:
        agent = self.session.get(Agent, agent_id)
        if agent is None:
            return None
        if agent.status == AgentStatus.stopped and agent.stop_reason == reason:
            return agent

        agent.status = AgentStatus.stopped
        agent.stop_reason = reason
        agent.stopped_at = now
        self.record_audit(
            agent_id=agent_id,
            target_date=now.date(),
            source=source,
            event_type='auto_stop',
            status='triggered',
            reason=reason,
            payload=payload,
        )

        pdca = self.session.scalar(select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == now.date()))
        if pdca is not None:
            summary = dict(pdca.analytics_summary or {})
            summary['auto_stop'] = {'reason': reason, 'source': source}
            pdca.analytics_summary = summary
        return agent
