#!/usr/bin/env bash
# Start a local one-microphone test setup on the central computer.
# Mic 1 uses the system default input. Mic 2 intentionally fails as MIC2.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

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

if ! python3 - <<'PY' >/dev/null 2>&1
import numpy
import sounddevice
import yaml
import pythonosc
import torch
import opensmile
import psutil
PY
then
    echo "ERROR: Python runtime dependencies are missing from $VENV_DIR" >&2
    echo "Install them with:" >&2
    echo "  $VENV_DIR/bin/python -m pip install -r requirements-pi.txt" >&2
    exit 1
fi

for cfg in config_local_mic1.yaml config_local_mic2.yaml config_features.yaml; do
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
launch_one config_local_mic1.yaml mic1 || failures=$((failures + 1))
launch_one config_local_mic2.yaml mic2 || failures=$((failures + 1))

if [ "$failures" -gt 0 ]; then
    echo ""
    echo "ERROR: $failures local microphone process failed to start. Check logs/mic1.log and logs/mic2.log." >&2
    exit 1
fi

echo ""
echo "Local test processing started. In the receiver menu you should see:"
echo "  Pi local / Mic 1  audio ok, using this computer's default input"
echo "  Pi local / Mic 2  audio failure, because MIC2 is intentionally absent"
echo ""
echo "Run ./run_web.sh in another terminal if the receiver is not already open."
echo "Tail logs with: tail -f logs/mic1.log logs/mic2.log"