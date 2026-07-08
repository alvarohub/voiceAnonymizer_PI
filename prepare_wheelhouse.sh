#!/usr/bin/env bash
# Build a local Python wheelhouse for offline Raspberry Pi installs.
# Role: Deployment Phase 1 helper (build wheelhouse).
# Runs on: One Raspberry Pi with internet access.
# Called by: Manual operator/developer command.
#
# Run this once on a Raspberry Pi that has internet access and the same
# Raspberry Pi OS / Python version as the offline Pis. This Pi downloads
# or builds Raspberry-Pi-compatible .whl files. The offline Pis later use
# those local files without internet.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/.wheelhouse-venv"
WHEELHOUSE_DIR="$SCRIPT_DIR/wheelhouse"

echo "==> Preparing wheelhouse build environment"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip wheel setuptools

mkdir -p "$WHEELHOUSE_DIR"

echo "==> Building/downloading wheels into $WHEELHOUSE_DIR"
python -m pip wheel --wheel-dir "$WHEELHOUSE_DIR" -r requirements-pi.txt
python -m pip wheel --wheel-dir "$WHEELHOUSE_DIR" pip wheel setuptools

echo
echo "Done. Keep this folder with the USB bundle:"
echo "    $WHEELHOUSE_DIR"