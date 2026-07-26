#!/usr/bin/env bash
# Start the UniFi Optimizer — backend + frontend in one shot.
# Usage: ./start.sh
# Stop:  Ctrl+C (kills both servers)

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID 2>/dev/null
    echo "Done."
}
trap cleanup EXIT INT TERM

# --- Backend (FastAPI on port 8000) ---
echo "Starting backend on http://localhost:8000 ..."
cd "$ROOT"
uvicorn server.main:app --reload --port 8000 &
BACKEND_PID=$!

# --- Frontend (Vite dev server on port 5173) ---
echo "Starting frontend on http://localhost:5173 ..."
cd "$ROOT/web"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "════════════════════════════════════════"
echo "  UniFi Optimizer is running!"
echo "  Open: http://localhost:5173"
echo "  API:  http://localhost:8000/docs"
echo "  Press Ctrl+C to stop both servers."
echo "════════════════════════════════════════"
echo ""

wait
