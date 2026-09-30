#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

uv python install 3.12
(
  cd backend
  uv sync
)
npm install --prefix frontend

# 单个 worker：进程内闸门和 RequestState 不能拆到多个进程。
(
  cd backend
  exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8001 --workers 1
) &
api_pid=$!
cleanup() {
  kill "$api_pid" 2>/dev/null || true
  wait "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready=0
for _ in $(seq 1 50); do
  if curl -sf http://127.0.0.1:8001/api/v1/health >/dev/null; then
    ready=1
    break
  fi
  sleep 0.2
done
if [[ "$ready" -ne 1 ]]; then
  echo "API 没有在 127.0.0.1:8001 起来" >&2
  exit 1
fi

npm run dev --prefix frontend
