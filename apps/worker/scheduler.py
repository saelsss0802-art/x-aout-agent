from __future__ import annotations


def placeholder_job() -> None:
    print("[worker] placeholder job executed")


def run_scheduler() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(placeholder_job, trigger="interval", seconds=30, id="placeholder")
    print("[worker] scheduler started")
    scheduler.start()
