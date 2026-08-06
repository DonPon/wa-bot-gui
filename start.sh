#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"

PORT="${PORT:-8787}"

if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/health" | grep -q "200"; then
  echo "El servidor ya está corriendo en el puerto ${PORT}."
  exit 0
fi

if [ ! -d .venv ]; then
  echo "Creando venv con uv..."
  uv sync
fi

nohup uv run python app.py >> wa-scheduler.out.log 2>&1 &
echo "WA Scheduler iniciado (PID $!)."
echo "Abre: http://127.0.0.1:${PORT}"
echo "Logs: wa-scheduler.out.log"