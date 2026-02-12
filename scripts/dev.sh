#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

alembic -c apps/api/alembic.ini upgrade head

python -m uvicorn apps.api.app.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" &
API_PID=$!

cleanup() {
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT

python -m apps.worker.main
