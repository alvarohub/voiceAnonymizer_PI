#!/usr/bin/env bash
# Stop the two strip_monitor instances launched by START_AUDIO_PROCESSING.sh.
# Sends SIGTERM, then SIGKILL after a 5s grace period.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

stop_one() {
    local tag="$1"
    local pidfile="logs/${tag}.pid"
    if [ ! -f "$pidfile" ]; then
        echo "[skip] $tag: no pidfile"
        return
    fi
    local pid
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
        echo "[stop] $tag pid=$pid (SIGTERM)"
        kill "$pid"
        for _ in 1 2 3 4 5; do
            sleep 1
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "[kill] $tag pid=$pid (SIGKILL)"
            kill -9 "$pid"
        fi
    else
        echo "[gone] $tag pid=$pid not running"
    fi
    rm -f "$pidfile"
}

stop_one mic1
stop_one mic2

# Fallback: kill any orphaned strip_monitor processes (in case PID files
# were lost or instances were started manually).
ORPHANS=$(pgrep -f "strip_monitor.py" || true)
if [ -n "$ORPHANS" ]; then
    echo "[orphans] killing leftover strip_monitor PIDs: $ORPHANS"
    # shellcheck disable=SC2086
    kill $ORPHANS 2>/dev/null || true
fi
echo "Done."
