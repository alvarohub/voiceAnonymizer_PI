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
#         ./run_web.sh --session start_recording_session.yaml
# Ctrl-C stops the bridge.

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

SESSION_FILE=""

print_usage() {
  cat <<'EOF'
Usage:
  ./run_web.sh
  ./run_web.sh --session start_recording_session.yaml

Options:
  -s, --session <yaml>   Expected rig YAML. The GUI will list all expected
                         Pi/mic processes and highlight missing heartbeats.
  -h, --help             Show this help message.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--session)
      if [ "$#" -lt 2 ]; then
        echo "ERROR: --session needs a YAML file path" >&2
        exit 1
      fi
      SESSION_FILE="$2"
      shift 2
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      print_usage >&2
      exit 1
      ;;
  esac
done

if [ -n "$SESSION_FILE" ]; then
  if ! EXPECTED_TARGETS_JSON="$({
    python3 - "$SCRIPT_DIR" "$SESSION_FILE" <<'PY'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
session_arg = sys.argv[2]
session = Path(session_arg)
if not session.is_absolute():
    from_cwd = (Path.cwd() / session).resolve()
    from_repo = (repo / session).resolve()
    if from_cwd.exists():
        session = from_cwd
    elif from_repo.exists():
        session = from_repo

if not session.exists():
    raise SystemExit(f"session YAML not found: {session_arg}")

sys.path.insert(0, str(repo))
from speech_control import load_session_plan

plan = load_session_plan(session)
targets = [
    {
        "device_id": t.device_id,
        "pi_id": t.pi_id,
        "mic_id": str(t.mic_id),
        "hostname": t.hostname,
        "ip": t.ip,
        "ctrl_port": int(t.ctrl_port),
    }
    for t in plan.targets
]
print(json.dumps(targets, separators=(",", ":")))
PY
  } 2>&1)"; then
    echo "ERROR: failed to load expected session targets from '$SESSION_FILE'" >&2
    echo "$EXPECTED_TARGETS_JSON" >&2
    exit 1
  fi
  export EXPECTED_TARGETS_JSON
  echo "[session] expected rig loaded from: $SESSION_FILE"
fi

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
