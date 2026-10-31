#!/usr/bin/env bash
# Restart the BP Tracker: stop any running instance, then start a fresh one.
set -euo pipefail
cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
LOG_FILE="${LOG_FILE:-start.log}"
PID_FILE="${PID_FILE:-start.pid}"

# stop a previous instance if one is running
pkill -f "uvicorn app.main:app" 2>/dev/null && echo "stopped previous instance" || true

uv sync --quiet
# load .env if present (SECRET_KEY, ...)
ENV_ARG=(); [ -f .env ] && ENV_ARG=(--env-file .env)
echo "starting on http://${HOST}:${PORT}/  (docs at /docs), logging to ${LOG_FILE}"
nohup uv run uvicorn app.main:app --host "$HOST" --port "$PORT" --reload "${ENV_ARG[@]}" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "started with pid $(cat "$PID_FILE") (stop with: kill \$(cat $PID_FILE))"
