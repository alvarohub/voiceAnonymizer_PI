#!/bin/bash
# Copy the SPEECH_RECORD_ANALYSIS repo to a USB drive (or any destination),
# skipping the massive Mac-only folders (.venv, receiver/node_modules, .git, etc.).
#
# Usage:
#   ./copy_to_usb.sh /Volumes/YOUR_USB_NAME
#
# Example:
#   ./copy_to_usb.sh /Volumes/SANDALVARO

set -e

if [ -z "$1" ]; then
    echo "ERROR: missing destination path."
    echo "Usage: $0 /Volumes/YOUR_USB_NAME"
    echo "Tip: run 'ls /Volumes/' to see mounted drives."
    exit 1
fi

DEST="$1/SPEECH_RECORD_ANALYSIS"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "Copying from: $SCRIPT_DIR"
echo "Copying to:   $DEST"
echo

rsync -avh --progress \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude 'venv/' \
    --exclude '__pycache__/' \
    --exclude 'receiver/node_modules/' \
    --exclude 'log_data/' \
    "$SCRIPT_DIR/" "$DEST/"

echo
echo "=============================================="
echo "Done. Destination size:"
du -sh "$DEST"
