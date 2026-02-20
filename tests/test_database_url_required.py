from __future__ import annotations

import os
import subprocess
import sys


ERROR_MESSAGE = "DATABASE_URL is required"


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = "packages/core"
    return env


def test_core_db_imports_without_database_url() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import core.db"],
        capture_output=True,
        text=True,
        env=_base_env(),
    )

    assert result.returncode == 0


def test_core_db_session_fails_without_database_url() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from core.db import SessionLocal; SessionLocal()"],
        capture_output=True,
        text=True,
        env=_base_env(),
    )

    assert result.returncode != 0
    assert ERROR_MESSAGE in result.stderr


def test_worker_stops_without_database_url() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "apps.worker.main", "--once"],
        capture_output=True,
        text=True,
        env=_base_env(),
    )

    assert result.returncode != 0
    assert ERROR_MESSAGE in result.stderr


def test_migration_stops_without_database_url() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", "head"],
        capture_output=True,
        text=True,
        env=_base_env(),
    )

    assert result.returncode != 0
    assert ERROR_MESSAGE in result.stderr
