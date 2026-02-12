#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

python - <<'PYCHK'
import importlib.util
import sys

if importlib.util.find_spec("core") is None:
    sys.stderr.write("core package is not installed. Run: pip install -e packages/core\n")
    raise SystemExit(1)
PYCHK

python -m uvicorn apps.api.app.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" &
API_PID=$!

cleanup() {
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT

python -m apps.worker.main
