#!/usr/bin/env bash
# Role: One-command fleet deploy wrapper with lab defaults.
# Runs on: Deployment/control machine.
# Calls: deploy_bundle_to_fleet.py (Phase 2+3) then configure_auto_start.py (Phase 4).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DEVICES_FILE="devices.csv"
DEVICES="1-6"
SSH_USER="pi"
WHEELHOUSE_SOURCE="pi@192.168.0.11:/home/pi/SPEECH_RECORD_ANALYSIS/wheelhouse/"

DRY_RUN=0
SKIP_AUTOSTART=0
NO_PULL_WHEELHOUSE=0
PASSWORD=""

usage() {
  cat <<'EOF'
Usage:
  ./deploy_lab_defaults.sh [options]

Default lab values:
  --devices-file devices.csv
  --devices 1-6
  --user pi
  --pull-wheelhouse pi@192.168.0.11:/home/pi/SPEECH_RECORD_ANALYSIS/wheelhouse/

Options:
  --dry-run                 Print commands only.
  --skip-autostart          Run only Phase 2+3 (no configure_auto_start.py).
  --no-pull-wheelhouse      Skip the wheelhouse pull step.
  --devices-file PATH       Override devices CSV path.
  --devices RANGE_OR_LIST   Override device indices (example: 1-6 or "1 2 3").
  --user NAME               SSH user for both scripts.
  --pull-wheelhouse SRC     Override wheelhouse rsync source.
  --password VALUE          Password for configure_auto_start.py (optional).
  -h, --help                Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --skip-autostart)
      SKIP_AUTOSTART=1
      shift
      ;;
    --no-pull-wheelhouse)
      NO_PULL_WHEELHOUSE=1
      shift
      ;;
    --devices-file)
      DEVICES_FILE="$2"
      shift 2
      ;;
    --devices)
      DEVICES="$2"
      shift 2
      ;;
    --user)
      SSH_USER="$2"
      shift 2
      ;;
    --pull-wheelhouse)
      WHEELHOUSE_SOURCE="$2"
      shift 2
      ;;
    --password)
      PASSWORD="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

phase23_cmd=(
  python3 deploy_bundle_to_fleet.py
  --devices-file "$DEVICES_FILE"
  --devices "$DEVICES"
  --user "$SSH_USER"
)

if [[ "$NO_PULL_WHEELHOUSE" -eq 0 ]]; then
  phase23_cmd+=(--pull-wheelhouse "$WHEELHOUSE_SOURCE")
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  phase23_cmd+=(--dry-run)
fi

echo "==> Running Phase 2 + 3 with lab defaults"
"${phase23_cmd[@]}"

if [[ "$SKIP_AUTOSTART" -eq 1 ]]; then
  echo "==> Skipping Phase 4 (--skip-autostart)"
  exit 0
fi

phase4_cmd=(
  python3 configure_auto_start.py
  --devices-file "$DEVICES_FILE"
  --devices "$DEVICES"
  --user "$SSH_USER"
)

if [[ -n "$PASSWORD" ]]; then
  phase4_cmd+=(--password "$PASSWORD")
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  phase4_cmd+=(--dry-run)
fi

echo ""
echo "==> Running Phase 4 (autostart services)"
"${phase4_cmd[@]}"

echo ""
echo "Done."