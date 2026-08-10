#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONPATH="${PWD}:${PYTHONPATH:-}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export DATABASE_URL="${DATABASE_URL:-postgresql://chat:chat@localhost:5432/chat_agent}"
export ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8000}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-mock}"

case "${1:-}" in
  api)
    exec uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
    ;;
  gateway)
    exec uvicorn gateway.main:app --reload --host 0.0.0.0 --port 8080
    ;;
  worker)
    exec celery -A orchestrator.celery_app.celery_app worker --loglevel=info
    ;;
  beat)
    exec celery -A orchestrator.celery_app.celery_app beat --loglevel=info
    ;;
  temporal)
    exec python -m temporal.worker
    ;;
  test)
    exec pytest tests/ -v
    ;;
  *)
    echo "Usage: ./scripts/run.sh [api|gateway|worker|beat|temporal|test]"
    exit 1
    ;;
esac
