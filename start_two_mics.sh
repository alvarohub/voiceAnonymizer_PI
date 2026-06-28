#!/usr/bin/env bash
# Launch two strip_monitor.py instances on this Pi — one per microphone.
# Reads config_mic1.yaml + config_mic2.yaml.
# Logs go to ./logs/mic{1,2}.log; PIDs to ./logs/mic{1,2}.pid.
# Use stop_two_mics.sh to terminate.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Auto-detect venv (same logic as start_audio_server.sh)
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
    nohup python3 strip_monitor.py --config "$cfg" >"$logfile" 2>&1 &
    echo $! >"$pidfile"
    sleep 0.5
    if ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        echo "[ERROR] $tag failed to start — see $logfile" >&2
        rm -f "$pidfile"
    fi
}

launch_one config_mic1.yaml mic1
launch_one config_mic2.yaml mic2

echo ""
echo "Both instances launched. Tail logs with:"
echo "  tail -f logs/mic1.log logs/mic2.log"
echo "Stop with: ./stop_two_mics.sh"
