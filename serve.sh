#!/usr/bin/env bash
set -euo pipefail

# Obsolete: the watchdog/tunnel loop has been replaced by systemd services
# plus nginx reverse-proxying on this VPS.
echo "serve.sh is obsolete. wa-bot now runs as a systemd service."
echo "Start it with: systemctl start wa-bot   (or ./start.sh)"
exit 0
