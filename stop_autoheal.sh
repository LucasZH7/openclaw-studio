#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f autoheal.pid ]]; then
  echo "autoheal.pid not found"
  exit 0
fi
PID=$(cat autoheal.pid)
if ps -p "$PID" >/dev/null 2>&1; then
  kill "$PID"
  echo "Autoheal stopped (PID $PID)"
else
  echo "Autoheal not running"
fi
rm -f autoheal.pid
