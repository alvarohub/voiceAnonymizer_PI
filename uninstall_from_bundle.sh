#!/usr/bin/env bash
# Uninstall helper for Speech Record Analysis on a target Pi.
#
# Default mode removes project-local artifacts only (services, processes, venv,
# logs, output, wheelhouse, debs, models) and keeps apt packages installed.
#
# Optional deep-clean mode (--purge-apt) also purges the apt packages listed in
# requirements-apt.txt.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  bash uninstall_from_bundle.sh [options]

Options:
  --project-dir PATH     Project directory to clean.
                         Default: /home/$USER/SPEECH_RECORD_ANALYSIS
  --remove-project-dir   Remove the full project directory at the end.
                         Default: keep folder and clean its contents only.
  --purge-apt            Also purge apt packages listed in requirements-apt.txt.
  --yes                  Non-interactive mode (skip confirmation prompt).
  -h, --help             Show this help.

Examples:
  bash uninstall_from_bundle.sh
  bash uninstall_from_bundle.sh --remove-project-dir
  bash uninstall_from_bundle.sh --remove-project-dir --purge-apt --yes
EOF
}

PROJECT_DIR="/home/${USER}/SPEECH_RECORD_ANALYSIS"
PURGE_APT=0
REMOVE_PROJECT_DIR=0
ASSUME_YES=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --project-dir)
            PROJECT_DIR="$2"
            shift 2
            ;;
        --purge-apt)
            PURGE_APT=1
            shift
            ;;
        --remove-project-dir)
            REMOVE_PROJECT_DIR=1
            shift
            ;;
        --yes)
            ASSUME_YES=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$PROJECT_DIR" ] || [ "$PROJECT_DIR" = "/" ]; then
    echo "ERROR: unsafe project directory: '$PROJECT_DIR'" >&2
    exit 2
fi

echo "==> Uninstall target"
echo "    project_dir: $PROJECT_DIR"
echo "    remove_project_dir: $REMOVE_PROJECT_DIR"
echo "    purge_apt: $PURGE_APT"

if [ "$ASSUME_YES" -ne 1 ]; then
    printf "Proceed with uninstall? [y/N] "
    read -r reply
    case "$reply" in
        y|Y|yes|YES)
            ;;
        *)
            echo "Aborted."
            exit 0
            ;;
    esac
fi

echo "==> Disk usage before cleanup"
if [ -d "$PROJECT_DIR" ]; then
    du -sh "$PROJECT_DIR" || true
    du -sh "$PROJECT_DIR"/venv "$PROJECT_DIR"/wheelhouse "$PROJECT_DIR"/models "$PROJECT_DIR"/debs "$PROJECT_DIR"/log_data "$PROJECT_DIR"/output 2>/dev/null || true
else
    echo "Project directory not found (nothing to clean there)."
fi

echo "==> Stopping speech-record user services (if present)"
set +e
systemctl --user disable --now speech-record-mic1.service speech-record-mic2.service >/dev/null 2>&1
systemctl --user daemon-reload >/dev/null 2>&1
set -e

echo "==> Removing speech-record service unit files"
rm -f "$HOME/.config/systemd/user/speech-record-mic1.service"
rm -f "$HOME/.config/systemd/user/speech-record-mic2.service"

echo "==> Killing runtime processes (if any)"
pkill -f strip_monitor.py || true
pkill -f audio_analysis_background.py || true

if [ -d "$PROJECT_DIR" ]; then
    if [ "$REMOVE_PROJECT_DIR" -eq 1 ]; then
        echo "==> Removing project directory: $PROJECT_DIR"
        rm -rf "$PROJECT_DIR"
    else
        echo "==> Cleaning project-local artifacts inside: $PROJECT_DIR"
        rm -rf "$PROJECT_DIR/venv"
        rm -rf "$PROJECT_DIR/wheelhouse"
        rm -rf "$PROJECT_DIR/debs"
        rm -rf "$PROJECT_DIR/models"
        rm -rf "$PROJECT_DIR/log_data"
        rm -rf "$PROJECT_DIR/output"
        rm -rf "$PROJECT_DIR/logs"
    fi
fi

if [ "$PURGE_APT" -eq 1 ]; then
    echo "==> Purging apt packages from requirements-apt.txt"
    if [ ! -f "$PROJECT_DIR/requirements-apt.txt" ]; then
        echo "ERROR: cannot find requirements-apt.txt at $PROJECT_DIR/requirements-apt.txt" >&2
        echo "Skip --purge-apt or provide --project-dir pointing to the project root." >&2
        exit 1
    fi

    mapfile -t apt_packages < <(sed 's/#.*//' "$PROJECT_DIR/requirements-apt.txt" | tr -d '\r' | awk 'NF')
    if [ "${#apt_packages[@]}" -gt 0 ]; then
        sudo apt-get purge -y "${apt_packages[@]}"
        sudo apt-get autoremove --purge -y
        sudo apt-get clean
    fi
fi

echo "==> Disk usage after cleanup"
if [ -d "$PROJECT_DIR" ]; then
    du -sh "$PROJECT_DIR" || true
    du -sh "$PROJECT_DIR"/venv "$PROJECT_DIR"/wheelhouse "$PROJECT_DIR"/models "$PROJECT_DIR"/debs "$PROJECT_DIR"/log_data "$PROJECT_DIR"/output 2>/dev/null || true
else
    echo "Project directory removed."
fi

echo "Done."
