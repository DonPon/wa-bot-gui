#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

SUBDOMAIN="${TUNNEL_SUBDOMAIN:-wabot}"
PORT="${TUNNEL_PORT:-8787}"
URL_FILE="tunnel.url"
LOG_FILE="tunnel.log"
PID_FILE="tunnel.pid"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

# Guarda el PID del propio wrapper para poder detenerlo desde stop.sh / serve.sh
echo "$$" > "$PID_FILE"
echo "" > "$URL_FILE"

LT_PID=""

stop() {
  log "deteniendo túnel"
  [ -n "$LT_PID" ] && kill "$LT_PID" 2>/dev/null
  pkill -f "localtunnel" 2>/dev/null
  rm -f "$PID_FILE"
  exit 0
}
trap 'stop' INT TERM

parse_url() {
  while IFS= read -r line; do
    log "$line"
    if echo "$line" | grep -q "your url is:"; then
      URL=$(echo "$line" | grep -o 'https://[^ ]*')
      echo "$URL" > "$URL_FILE"
      log "URL activa: $URL"
    fi
  done
}

while true; do
  log "iniciando localtunnel -> https://${SUBDOMAIN}.loca.lt hacia :${PORT}"
  npx --yes localtunnel --port "$PORT" --subdomain "$SUBDOMAIN" > >(parse_url) 2>&1 &
  LT_PID=$!
  wait "$LT_PID"
  LT_PID=""
  log "localtunnel terminó inesperadamente, reintentando en 5s..."
  sleep 5
done
