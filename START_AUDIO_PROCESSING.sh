#!/usr/bin/env bash
# Start the standard two-microphone audio processing setup on this Pi.
# Launches one strip_monitor.py process for MIC1 and one for MIC2.
# Logs go to ./logs/mic{1,2}.log; PIDs to ./logs/mic{1,2}.pid.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Auto-detect venv (this folder first, then $HOME)
VENV_DIR=""
for cand in "$SCRIPT_DIR/venv" "$SCRIPT_DIR/.venv" "$HOME/venv" "$HOME/.venv"; do
    if [ -f "$cand/bin/activate" ]; then
        VENV_DIR="$cand"
        break
    fi
done
if [ -z "$VENV_DIR" ]; then
    echo "ERROR: no Python venv found. Tried: ./venv, ./.venv, ~/venv, ~/.venv" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
echo "Using venv: $VENV_DIR"

for cfg in config_mic1.yaml config_mic2.yaml config_features.yaml; do
    if [ ! -f "$cfg" ]; then
        echo "ERROR: missing required config file: $cfg" >&2
        exit 1
    fi
done

mkdir -p logs

launch_one() {
    local cfg="$1"
    local tag="$2"
    local pidfile="logs/${tag}.pid"
    local logfile="logs/${tag}.log"

    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "[skip] $tag already running (pid $(cat "$pidfile"))"
        return
    fi

    echo "[start] $tag  cfg=$cfg  log=$logfile"
    nohup python3 -u strip_monitor.py --config "$cfg" --features-config config_features.yaml >"$logfile" 2>&1 &
    echo $! >"$pidfile"
    sleep 0.5
    if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "[ERROR] $tag failed to start - see $logfile" >&2
        rm -f "$pidfile"
        return 1
    fi
}

failures=0
launch_one config_mic1.yaml mic1 || failures=$((failures + 1))
launch_one config_mic2.yaml mic2 || failures=$((failures + 1))

if [ "$failures" -gt 0 ]; then
    echo ""
    echo "ERROR: $failures microphone process failed to start. Check logs/mic1.log and logs/mic2.log." >&2
    exit 1
fi

echo ""
echo "Audio processing started for MIC1 and MIC2. Tail logs with:"
echo "  tail -f logs/mic1.log logs/mic2.log"