#!/bin/bash
cd "$(dirname "$0")"

EXPECTED_VERSION="2.1.0"
LOOPBACK="127.0.0.1"

if [ ! -d ".venv" ]; then
  echo "Eerste start: virtuele Python-omgeving wordt aangemaakt..."
  python3 -m venv .venv || exit 1
  source .venv/bin/activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt || exit 1
else
  source .venv/bin/activate
fi

APPPORT=$(python - <<'PY'
import socket
for port in range(5050, 5100):
    s=socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        s.close()
        continue
    s.close()
    print(port)
    break
PY
)

if [ -z "$APPPORT" ]; then
  echo "Geen vrije poort gevonden tussen 5050 en 5099."
  exit 1
fi

if [ "$APPPORT" != "5050" ]; then
  echo ""
  echo "LET OP: poort 5050 is al bezet, waarschijnlijk door een oudere versie."
  echo "Deze versie start daarom op poort $APPPORT."
  echo ""
fi

mkdir -p storage
echo "$APPPORT" > storage/.server.port
export PSB_PORT="$APPPORT"
export PSB_LOCAL_LAUNCH="1"

echo "Praktijk Schitter Beheer v$EXPECTED_VERSION wordt gestart vanuit:"
pwd

python app.py &
SERVERPID=$!
echo "$SERVERPID" > storage/.server.pid

python - <<PY
import json, time, urllib.request, sys
url="http://${LOOPBACK}:${APPPORT}/api/health"
for _ in range(10):
    try:
        data=json.load(urllib.request.urlopen(url, timeout=2))
        if data.get("version") != "${EXPECTED_VERSION}":
            print("VERKEERDE VERSIE ACTIEF:", data.get("version"))
            sys.exit(2)
        print("Actief: v"+data["version"]+" op poort ${APPPORT}")
        sys.exit(0)
    except Exception:
        time.sleep(1)
print("De nieuwe server reageert niet.")
sys.exit(1)
PY

if [ $? -ne 0 ]; then
  echo "Versiecontrole mislukt. Browser wordt niet geopend."
  kill "$SERVERPID" 2>/dev/null
  exit 1
fi

open "http://${LOOPBACK}:${APPPORT}"
wait "$SERVERPID"
