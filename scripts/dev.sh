#!/usr/bin/env bash
# Start the API (uvicorn, :8000, auto-reload) and the web app (Vite, :5173, proxies /api). Ctrl-C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT/backend/src"
API_PORT="${PAINTREEL_API_PORT:-8000}"
UI_PORT="${PAINTREEL_UI_PORT:-5173}"

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "warning: ffmpeg/ffprobe not found on PATH — the app will show an error until installed (brew install ffmpeg)" >&2
fi
for port in "$API_PORT" "$UI_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is already in use — PaintReel (or something else) is already running." >&2
    echo "Open http://127.0.0.1:$UI_PORT, or stop the other process first (Ctrl-C in its terminal)." >&2
    exit 1
  fi
done

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

(cd "$ROOT/backend" && "$ROOT/backend/.venv/bin/uvicorn" paintreel.api.app:create_app --factory \
  --reload --reload-dir src --host 127.0.0.1 --port "$API_PORT") &
(cd "$ROOT/frontend" && PAINTREEL_API="http://127.0.0.1:$API_PORT" npm run dev -- --host 127.0.0.1 --port "$UI_PORT" --strictPort) &

echo "PaintReel: open http://127.0.0.1:$UI_PORT  (API on :$API_PORT)"
wait
