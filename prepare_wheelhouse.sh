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
REQ_FILE="$SCRIPT_DIR/requirements-pi.txt"
VALIDATE_VENV_DIR="$SCRIPT_DIR/.wheelhouse-validate-venv"

echo "==> Preparing wheelhouse build environment"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip wheel setuptools

mkdir -p "$WHEELHOUSE_DIR"

echo "==> Clearing existing wheelhouse (*.whl) for deterministic rebuild"
rm -f "$WHEELHOUSE_DIR"/*.whl

echo "==> Building/downloading wheels into $WHEELHOUSE_DIR"
python -m pip wheel --wheel-dir "$WHEELHOUSE_DIR" -r "$REQ_FILE"
python -m pip wheel --wheel-dir "$WHEELHOUSE_DIR" pip wheel setuptools

echo "==> Validating wheelhouse with a strict offline install test"
rm -rf "$VALIDATE_VENV_DIR"
python3 -m venv "$VALIDATE_VENV_DIR"
# shellcheck disable=SC1091
source "$VALIDATE_VENV_DIR/bin/activate"
python -m pip install --no-index --find-links "$WHEELHOUSE_DIR" -r "$REQ_FILE"
python -m pip check
deactivate || true
rm -rf "$VALIDATE_VENV_DIR"

WHEEL_COUNT="$(find "$WHEELHOUSE_DIR" -maxdepth 1 -type f -name '*.whl' | wc -l | tr -d ' ')"

echo "==> Wheelhouse validation passed"
echo "==> Total wheels: $WHEEL_COUNT"

echo
echo "Done. Keep this folder with the USB bundle:"
echo "    $WHEELHOUSE_DIR"