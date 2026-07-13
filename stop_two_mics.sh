#!/usr/bin/env bash
# Backward-compatible wrapper.
# Canonical local cleanup now lives in fresh_start_local.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[info] stop_two_mics.sh is a compatibility wrapper."
echo "[info] Delegating to ./fresh_start_local.sh --mics-only"
exec ./fresh_start_local.sh --mics-only "$@"
