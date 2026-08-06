#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTACTOS_JSON="$SCRIPT_DIR/contactos.json"
LOG_BOT="${LOG_BOT:-$SCRIPT_DIR/wa-bot.log}"

DRY_RUN=0
PERSONA=""

for arg in "$@"; do
  case "$arg" in
    --dry-run|--dry)
      DRY_RUN=1
      ;;
    gaby|angelo|abuela)
      PERSONA="$arg"
      ;;
    *)
      echo "Uso: $0 {gaby|angelo|abuela} [--dry-run]" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PERSONA" ]]; then
  echo "Error: debes indicar a quién (gaby, angelo o abuela)." >&2
  echo "Uso: $0 {gaby|angelo|abuela} [--dry-run]" >&2
  exit 1
fi

CONTACTO="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$CONTACTOS_JSON" "$PERSONA")"

MSG_FILE="$SCRIPT_DIR/muestras-$PERSONA.txt"
if [[ ! -f "$MSG_FILE" ]]; then
  echo "Error: no existe el archivo $MSG_FILE" >&2
  exit 1
fi

TOTAL="$(awk 'NF' "$MSG_FILE" | wc -l | tr -d ' ')"
if [[ "$TOTAL" -eq 0 ]]; then
  echo "Error: el archivo $MSG_FILE está vacío." >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  SLEEP_SEC=$(( (RANDOM % 3) + 1 ))
else
  SLEEP_SEC=$(( (RANDOM % 1741) + 60 ))
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] $PERSONA: esperando ${SLEEP_SEC}s ($(( SLEEP_SEC / 60 ))min)..."
sleep "$SLEEP_SEC"

MENSAJE="$(awk -v n=$(( (RANDOM % TOTAL) + 1 )) 'NF && ++c==n{print; exit}' "$MSG_FILE")"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] $PERSONA: contacto=$CONTACTO mensaje=$MENSAJE"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY RUN] se habría ejecutado:"
  echo "  wacli send text --to $CONTACTO --message \"$MENSAJE\""
  exit 0
fi

wacli send text --to "$CONTACTO" --message "$MENSAJE" >> "$LOG_BOT" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] $PERSONA: mensaje enviado."
