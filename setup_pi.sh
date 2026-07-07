#!/usr/bin/env bash
# One-shot installer for the Speech Record Analysis streamer on Raspberry Pi.
# Tested target: Raspberry Pi OS (Debian 12/13, aarch64) on Pi 4 / Pi 5.
#
# What it does:
#   1. Install system packages required by sounddevice / torchaudio / git.
#   2. Create a Python virtual environment at ./venv (isolated from system Python).
#   3. Install Python packages from requirements-pi.txt.
#
# Idempotent: safe to re-run. Will skip already-installed apt packages.
# Run from inside the repository directory:
#     bash setup_pi.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
WHEELHOUSE_DIR="${WHEELHOUSE_DIR:-$SCRIPT_DIR/wheelhouse}"

echo "==> 1/3  System packages (apt)"
if [ "${SKIP_APT:-0}" = "1" ]; then
    echo "Skipping apt step because SKIP_APT=1"
else
    sudo apt update
    sudo apt install -y \
        python3-venv python3-pip python3-dev \
        portaudio19-dev libportaudio2 \
        libsndfile1 \
        ffmpeg \
        git build-essential
fi

echo "==> 2/3  Python virtual environment (./venv)"
if [ ! -d venv ]; then
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "==> 3/3  Python packages (requirements-pi.txt)"
# Note: torch will pull ~2 GB of unused nvidia-cuda-* libraries. This is
# wasted disk but harmless at runtime. See requirements-pi.txt for context.
if [ -d "$WHEELHOUSE_DIR" ] && find "$WHEELHOUSE_DIR" -type f -name '*.whl' -print -quit | grep -q .; then
    echo "Using offline wheelhouse: $WHEELHOUSE_DIR"
    pip install --no-index --find-links "$WHEELHOUSE_DIR" -r requirements-pi.txt
else
    pip install --upgrade pip wheel setuptools
    pip install -r requirements-pi.txt
fi

echo
echo "Done. To use:"
echo "    source venv/bin/activate"
echo "    python audio_analysis_background.py --list-devices"
