#!/usr/bin/env bash
set -euo pipefail

# wa-bot is managed by systemd on this VPS.
exec sudo -n systemctl start wa-bot
