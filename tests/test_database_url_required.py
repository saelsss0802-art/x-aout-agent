from __future__ import annotations

import os
import subprocess
import sys


def test_core_db_imports_without_database_url() -> None:
    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = "packages/core"

    result = subprocess.run(
        [sys.executable, "-c", "import core.db"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
