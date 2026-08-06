#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8787}"

# 1) Detener serve.sh si está corriendo (su trap limpia el túnel y evita que
#    el watchdog reinicie el server/túnel inmediatamente después).
pkill -f "serve.sh" 2>/dev/null

# 2) Detener el túnel de forma directa y definitiva (wrapper + localtunnel).
if [ -f tunnel.pid ]; then
  TPID=$(cat tunnel.pid 2>/dev/null || true)
  [ -n "${TPID:-}" ] && kill "$TPID" 2>/dev/null
fi
pkill -f "tunnel.sh" 2>/dev/null
pkill -f "localtunnel" 2>/dev/null

# 3) Detener el servidor.
pkill -f "wa-scheduler/.venv/bin/python.*app.py" 2>/dev/null
pkill -f "uv run python app.py" 2>/dev/null

sleep 1
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/health" | grep -q "200"; then
  echo "Aún está corriendo. Detenlo manualmente con: pkill -f app.py"
  exit 1
fi
echo "Server y túnel detenidos."
