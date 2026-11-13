#!/bin/sh
# Virtual display + noVNC + loopback forwards to the sibling containers, then the Electron app.
# The app's main process only accepts loopback backend/agent URLs, so socat exposes the compose services on 127.0.0.1.
set -eu

BACKEND_HOST="${SPINE_UI_BACKEND_HOST:-backend}"
BACKEND_PORT="${SPINE_UI_BACKEND_PORT:-8000}"
AGENT_HOST="${SPINE_UI_AGENT_HOST:-agent}"
AGENT_PORT="${SPINE_UI_AGENT_PORT:-8003}"

export ASCLEPIUS_BACKEND_URL="${ASCLEPIUS_BACKEND_URL:-http://127.0.0.1:$BACKEND_PORT}"
export ASCLEPIUS_AGENT_URL="${ASCLEPIUS_AGENT_URL:-http://127.0.0.1:$AGENT_PORT}"
# Both services must receive the same explicit token; fail closed if it is missing.
: "${ASCLEPIUS_BACKEND_TOKEN:?Set SPINE_APP_UI_TOKEN for the backend and UI}"
: "${SPINE_UI_VNC_PASSWORD:?Set SPINE_UI_VNC_PASSWORD}"
if [ "${#SPINE_UI_VNC_PASSWORD}" -ne 8 ]; then
  echo 'SPINE_UI_VNC_PASSWORD must contain exactly 8 characters' >&2
  exit 1
fi
umask 077
password_file=$(mktemp)
printf '%s\n' "$SPINE_UI_VNC_PASSWORD" > "$password_file"
unset SPINE_UI_VNC_PASSWORD
if [ -z "${ASCLEPIUS_AGENT_TOKEN:-}" ] && [ -r /agent-data/agent-http-token ]; then
  export ASCLEPIUS_AGENT_TOKEN_FILE=/agent-data/agent-http-token
fi

pids=""
cleanup() { for p in $pids; do kill "$p" 2>/dev/null || true; done; rm -f "$password_file"; }
trap cleanup EXIT INT TERM

socat TCP-LISTEN:"$BACKEND_PORT",bind=127.0.0.1,fork,reuseaddr TCP:"$BACKEND_HOST":"$BACKEND_PORT" &
pids="$pids $!"
socat TCP-LISTEN:"$AGENT_PORT",bind=127.0.0.1,fork,reuseaddr TCP:"$AGENT_HOST":"$AGENT_PORT" &
pids="$pids $!"

Xvfb "$DISPLAY" -screen 0 "$SCREEN_GEOMETRY" -nolisten tcp &
pids="$pids $!"
i=0
until [ -e "/tmp/.X11-unix/X${DISPLAY#:}" ] || [ "$i" -ge 50 ]; do i=$((i + 1)); sleep 0.1; done

x11vnc -display "$DISPLAY" -forever -shared -passwdfile "$password_file" -localhost -quiet &
pids="$pids $!"
websockify --web=/usr/share/novnc "$NOVNC_PORT" 127.0.0.1:5900 &
pids="$pids $!"

# --no-sandbox: Chromium's process sandbox needs user namespaces, which containers usually lack (the container is the boundary).
# --password-store=basic: no keyring in the container, so safeStorage falls back to Chromium's basic backend.
cd /app
node_modules/.bin/electron . --no-sandbox --disable-gpu --password-store=basic &
app=$!
pids="$pids $app"
wait "$app"
