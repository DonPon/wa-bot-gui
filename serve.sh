#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8787}"
LOG="serve.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

kill_tunnel() {
  if [ -f tunnel.pid ]; then
    TPID=$(cat tunnel.pid 2>/dev/null || true)
    [ -n "${TPID:-}" ] && kill "$TPID" 2>/dev/null
  fi
  pkill -f "tunnel.sh" 2>/dev/null
  pkill -f "localtunnel" 2>/dev/null
}

cleanup() {
  kill_tunnel
  log "detenido"
  exit 0
}
trap cleanup INT TERM

command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock

if ! curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/health" | grep -q "200"; then
  ./start.sh
  log "server iniciado (puerto ${PORT})"
else
  log "server ya estaba corriendo"
fi

# Limpia túneles huérfanos de una sesión anterior para no dejar el subdominio ocupado.
kill_tunnel
nohup ./tunnel.sh >/dev/null 2>&1 &
TUNNEL_PID=$!
log "túnel iniciado (pid ${TUNNEL_PID})"

while true; do
  if ! curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/health" | grep -q "200"; then
    log "server caído, relanzando..."
    ./start.sh
  fi
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    log "túnel caído, relanzando..."
    kill_tunnel
    nohup ./tunnel.sh >/dev/null 2>&1 &
    TUNNEL_PID=$!
  fi
  sleep 10
done
