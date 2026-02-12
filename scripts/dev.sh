#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

if [ -z "${VIRTUAL_ENV:-}" ]; then
  echo "[ERROR] Python virtual environment is not active."
  echo "Please run setup first:"
  echo "  python -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  echo "  pip install -e packages/core"
  exit 1
fi

if ! python -c "import alembic, sqlalchemy, psycopg, uvicorn, core" >/dev/null 2>&1; then
  echo "[ERROR] Required Python packages are missing in the current venv."
  echo "Please run setup first:"
  echo "  python -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt"
  echo "  pip install -e packages/core"
  exit 1
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "[ERROR] DATABASE_URL is not set."
  echo "Set Supabase Postgres URL in .env (SQLAlchemy format):"
  echo "  postgresql+psycopg://USER:PASSWORD@HOST:PORT/DBNAME"
  exit 1
fi

python -m alembic -c apps/api/alembic.ini upgrade head

python -m uvicorn apps.api.app.main:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" &
API_PID=$!

cleanup() {
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT

python -m apps.worker.main
