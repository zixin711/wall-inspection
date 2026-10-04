#!/usr/bin/env bash
# Start the service. Run ./setup.sh first.
#   ./run.sh            Start with HTTP
#   ./run.sh --https    Start with HTTPS for mobile live preview and alignment
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] || { echo "No virtual environment found; run ./setup.sh first."; exit 1; }
[ -f .env ] || cp .env.example .env

PORT=$(grep -E '^APP_PORT=' .env | cut -d= -f2 | tr -d ' \r')
PORT="${PORT:-8000}"

ip=$( (ipconfig getifaddr en0 2>/dev/null) \
   || (hostname -I 2>/dev/null | awk '{print $1}') \
   || echo 127.0.0.1 )

USE_TLS=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --https) USE_TLS=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

if [ "$USE_TLS" = "1" ]; then
  [ -f certs/cert.pem ] || scripts/make_cert.sh "$ip"
  echo "Service URL:  https://127.0.0.1:$PORT"
  echo "Mobile URL:   https://$ip:$PORT"
  echo "The self-signed certificate may trigger a warning on first access."
  echo
  exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" \
       --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem ${ARGS[@]+"${ARGS[@]}"}
fi

echo "Service URL:  http://127.0.0.1:$PORT"
echo "Mobile URL:   http://$ip:$PORT"
echo "On mobile HTTP, browser live preview is unavailable. The page can open the"
echo "system camera instead; use ./run.sh --https for live preview and alignment."
echo

exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" ${ARGS[@]+"${ARGS[@]}"}
