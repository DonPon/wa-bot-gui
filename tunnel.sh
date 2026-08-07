#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

# Tu dominio estático de ngrok (se puede sobreescribir con NGROK_DOMAIN).
NGROK_DOMAIN="${NGROK_DOMAIN:-impish-detest-epidermis}"
PORT="${TUNNEL_PORT:-8787}"
URL_FILE="tunnel.url"
LOG_FILE="tunnel.log"
PID_FILE="tunnel.pid"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

# Guarda el PID del propio wrapper para poder detenerlo desde stop.sh / serve.sh
echo "$$" > "$PID_FILE"
echo "" > "$URL_FILE"

NGROK_PID=""

stop() {
  log "deteniendo túnel"
  [ -n "$NGROK_PID" ] && kill "$NGROK_PID" 2>/dev/null
  pkill -f "ngrok" 2>/dev/null
  rm -f "$PID_FILE"
  exit 0
}
trap 'stop' INT TERM

# Normaliza el dominio: acepta "subdominio", "subdominio.ngrok-free.app/dev" o la URL completa.
build_url() {
  local d="$NGROK_DOMAIN"
  if [ -z "$d" ]; then
    NGROK_URL=""
  elif echo "$d" | grep -q "://"; then
    NGROK_URL="$d"
  elif echo "$d" | grep -qE "\.ngrok-free\.(app|dev)$"; then
    NGROK_URL="https://$d"
  else
    NGROK_URL="https://$d.ngrok-free.dev"
  fi
}
build_url

# Lee la URL pública desde la API local de ngrok y la guarda en tunnel.url
write_url() {
  local url
  url=$(curl -s --max-time 5 "http://127.0.0.1:4040/api/tunnels" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    urls = [t.get("public_url", "") for t in data.get("tunnels", []) if t.get("public_url", "").startswith("https")]
    print(urls[0] if urls else "")
except Exception:
    print("")
' 2>/dev/null || true)
  if [ -n "$url" ]; then
    echo "$url" > "$URL_FILE"
    log "URL activa: $url"
  fi
}

while true; do
  if [ -n "$NGROK_URL" ]; then
    log "iniciando ngrok -> $NGROK_URL hacia :${PORT}"
    nohup ngrok http "$PORT" --url "$NGROK_URL" --log=stdout --log-format=json >> "$LOG_FILE" 2>&1 &
  else
    log "iniciando ngrok (dominio aleatorio) hacia :${PORT}"
    nohup ngrok http "$PORT" --log=stdout --log-format=json >> "$LOG_FILE" 2>&1 &
  fi
  NGROK_PID=$!
  for i in $(seq 1 15); do
    sleep 1
    write_url
    [ -s "$URL_FILE" ] && break
  done
  wait "$NGROK_PID"
  NGROK_PID=""
  log "ngrok terminó inesperadamente, reintentando en 5s..."
  sleep 5
done
