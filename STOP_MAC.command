#!/bin/bash
cd "$(dirname "$0")"
if [ ! -f "storage/.server.pid" ]; then
  echo "Geen server-PID gevonden voor deze map."
  exit 0
fi
PID=$(cat storage/.server.pid)
kill "$PID" 2>/dev/null || true
rm -f storage/.server.pid storage/.server.port
echo "Server van deze map is gestopt."
