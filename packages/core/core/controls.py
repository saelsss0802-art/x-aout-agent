from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ActionType, CostLog, EngagementAction


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
                    llm_cost=self._llm_reserved,
                    image_gen_cost=Decimal("0"),
                    total=total_reserved,
                )
            )
        else:
            cost.x_api_cost = Decimal(cost.x_api_cost) + self._x_reserved
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
