#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f autoheal.pid ]]; then
  OLD=$(cat autoheal.pid)
  if ps -p "$OLD" >/dev/null 2>&1; then
    echo "Autoheal already running (PID $OLD)"
    exit 0
  fi
fi

/usr/bin/nohup /usr/bin/python3 autoheal.py </dev/null >/dev/null 2>&1 &
PID=$!
echo "$PID" > autoheal.pid
sleep 1
if ps -p "$PID" >/dev/null 2>&1; then
  echo "Autoheal started (PID $PID)"
else
  echo "Autoheal failed to start"
  exit 1
fi
