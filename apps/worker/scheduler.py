from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from core.db import Base, SessionLocal, engine
from core.models import Agent, AgentStatus, DailyPDCA

from .daily_routine import BudgetExceededError, run_daily_routine


def _require_database_url() -> None:
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required")


def _target_date(base_date: date) -> date:
    return base_date - timedelta(days=2)


def _write_error_log(agent_id: int, target_date: date, error_payload: dict[str, Any]) -> Path:
    log_dir = Path("apps/worker/logs") / str(agent_id)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{target_date.isoformat()}.json"
    payload = {
        "agent_id": agent_id,
        "target_date": target_date.isoformat(),
        "error": error_payload,
    }
    log_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return log_path


def _record_pdca_error(agent_id: int, target_date: date, error_payload: dict[str, Any]) -> None:
    with SessionLocal() as session:
        pdca = session.scalar(
            select(DailyPDCA).where(DailyPDCA.agent_id == agent_id, DailyPDCA.date == target_date)
        )
        if pdca is None:
            pdca = DailyPDCA(
                agent_id=agent_id,
                date=target_date,
                analytics_summary={"error": error_payload},
                analysis={"status": "failed"},
                strategy={},
                posts_created=[],
            )
            session.add(pdca)
        else:
            analytics_summary = dict(pdca.analytics_summary or {})
            analytics_summary["error"] = error_payload
            pdca.analytics_summary = analytics_summary
        session.commit()


def run_all_agents(base_date: date | None = None) -> list[dict[str, Any]]:
    _require_database_url()
    run_date = base_date or date.today()
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        active_agent_ids = session.scalars(
            select(Agent.id).where(Agent.status == AgentStatus.active).order_by(Agent.id.asc())
        ).all()

    results: list[dict[str, Any]] = []
    target_date = _target_date(run_date)
    for agent_id in active_agent_ids:
        try:
            result = run_daily_routine(agent_id=agent_id, base_date=run_date)
            status_payload = {
                "event": "agent_daily_routine",
                "status": "success",
                "agent_id": agent_id,
                "target_date": result["target_date"].isoformat(),
                "log_path": str(result["log_path"]),
            }
        except BudgetExceededError as exc:
            error_payload = {"type": type(exc).__name__, "message": str(exc)}
            log_path = _write_error_log(agent_id=agent_id, target_date=target_date, error_payload=error_payload)
            status_payload = {
                "event": "agent_daily_routine",
                "status": "skip",
                "reason": "skipped_budget",
                "agent_id": agent_id,
                "target_date": target_date.isoformat(),
                "log_path": str(log_path),
            }
        except Exception as exc:  # noqa: BLE001
            error_payload = {"type": type(exc).__name__, "message": str(exc)}
            _record_pdca_error(agent_id=agent_id, target_date=target_date, error_payload=error_payload)
            log_path = _write_error_log(agent_id=agent_id, target_date=target_date, error_payload=error_payload)
            status_payload = {
                "event": "agent_daily_routine",
                "status": "failed",
                "agent_id": agent_id,
                "target_date": target_date.isoformat(),
                "log_path": str(log_path),
                "error": error_payload,
            }

        results.append(status_payload)
        print(json.dumps(status_payload, ensure_ascii=True))

    return results


def _count_active_agents() -> int:
    with SessionLocal() as session:
        return len(
            session.scalars(select(Agent.id).where(Agent.status == AgentStatus.active).order_by(Agent.id.asc())).all()
        )


def run_scheduler() -> None:
    _require_database_url()
    tz_name = os.getenv("WORKER_TZ", "UTC")
    timezone = ZoneInfo(tz_name)
    hour = int(os.getenv("WORKER_DAILY_HOUR", "9"))
    minute = int(os.getenv("WORKER_DAILY_MINUTE", "0"))

    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        lambda: run_all_agents(base_date=date.today()),
        trigger="cron",
        hour=hour,
        minute=minute,
        id="daily-routine-all-agents",
        replace_existing=True,
    )

    active_count = _count_active_agents()
    next_run = scheduler.get_job("daily-routine-all-agents").next_run_time
    print(
        json.dumps(
            {
                "event": "scheduler_start",
                "next_run_time": next_run.isoformat() if next_run else None,
                "active_agent_count": active_count,
                "timezone": tz_name,
            },
            ensure_ascii=True,
        )
    )
    scheduler.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily worker scheduler")
    parser.add_argument("--once", action="store_true", help="Run all active agents once and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.once:
        run_all_agents(base_date=date.today())
        return
    run_scheduler()


if __name__ == "__main__":
    main()
