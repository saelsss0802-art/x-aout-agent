from __future__ import annotations

from core.interfaces import WorkerJob


def placeholder_job() -> None:
    print("[worker] placeholder job executed")


def run_scheduler(job: WorkerJob = placeholder_job) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(job, trigger="interval", seconds=30, id="placeholder")
    print("[worker] scheduler started")
    scheduler.start()
