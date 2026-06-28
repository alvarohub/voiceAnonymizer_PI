#!/usr/bin/env bash
# Mac-side launcher: OSC→WebSocket bridge + browser UI.
#
# Audio analysis runs ELSEWHERE (typically on the Raspberry Pi running
# strip_monitor.py). This script only:
#   1. starts the Node bridge on UDP 9000 / WS 8765 / HTTP 3000
#   2. opens the browser at http://localhost:3000
#
# All processing stages (VAD / prosody / emotion / OSC streaming) start
# inactive on the Pi by default; activate them from the browser buttons.
#
# Usage:  ./run_web.sh
# Ctrl-C stops the bridge.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/receiver"

# First-run: install node deps if missing.
if [ ! -d node_modules ]; then
  echo "[setup] installing node deps…"
  npm install
fi

# Open the browser first. The page retries the WS connection until the
# bridge is up, so launch order doesn't matter.
URL="http://localhost:3000"
case "$(uname -s)" in
  Darwin) (sleep 1 && open "$URL") & ;;
  Linux)  (sleep 1 && xdg-open "$URL" >/dev/null 2>&1) & ;;
  *)      echo "[info] open this in your browser: $URL" ;;
esac

echo ""
echo "  Bridge: UDP 9000 ← Pi   |   WS 8765 → browser   |   HTTP $URL"
echo "  Ctrl-C to stop."
echo ""

# Foreground so Ctrl-C cleans up.
exec node bridge.js
