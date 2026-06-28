#!/usr/bin/env bash
# Start the speech-to-emotion audio processing server on the Raspberry Pi.
# Activates the local Python venv and launches strip_monitor.py.
# Usage: ./start_audio_server.sh [extra args forwarded to strip_monitor.py]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Auto-detect venv (try common names, in this folder first then $HOME)
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
echo "Using venv: $VENV_DIR"

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

exec python3 strip_monitor.py "$@"
