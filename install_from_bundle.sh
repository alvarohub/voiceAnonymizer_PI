#!/usr/bin/env bash
# Operator-facing install script for a prepared USB/offline bundle.
# Role: Deployment Phase 3 helper (install on target Pi).
# Runs on: Each target Raspberry Pi.
# Called by: deploy_bundle_to_fleet.py, deploy_lab_defaults.sh, or manual command.
#
# Use this after copying SPEECH_RECORD_ANALYSIS/ from the USB drive onto the Pi.
# The bundle is expected to already contain models/ and wheelhouse/.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

missing=0
system_missing=()

require_path() {
    local path="$1"
    local description="$2"
    if [ ! -e "$path" ]; then
        echo "ERROR: missing $description: $path" >&2
        missing=1
    fi
}

require_path "models/iic/emotion2vec_plus_base/model.pt" "emotion model"
require_path "models/silero-vad/hubconf.py" "Silero VAD bundle"

if ! find wheelhouse -type f -name '*.whl' -print -quit 2>/dev/null | grep -q .; then
    echo "ERROR: missing offline Python wheelhouse: wheelhouse/*.whl" >&2
    missing=1
fi

if [ "$missing" -ne 0 ]; then
    cat >&2 <<'EOF'

This does not look like a fully prepared USB bundle.
Ask the USB preparer to include:
  - models/iic/emotion2vec_plus_base/model.pt
  - models/silero-vad/
  - wheelhouse/*.whl

EOF
    exit 1
fi

# --- Offline install of system .deb packages from ./debs/ (if present) --------
# The fleet Pis have no internet. debs/ carries every OS-level package (with
# transitive deps) that prepare_debs.sh downloaded on the builder Pi.
#
#   - `apt install ./file.deb` (with the leading `./`) treats file paths as
#     local packages and resolves inter-package dependencies across them.
#   - `--no-download` forbids apt from hitting the network; a missing dep fails
#     loudly and points at an incomplete bundle instead of silently silently
#     stalling on a DNS timeout.
#   - Skipped entirely if ./debs/ is empty, so first-time builder Pis or Pis
#     that already have all system packages continue to work.
DEBS_DIR="$SCRIPT_DIR/debs"
if compgen -G "$DEBS_DIR/*.deb" > /dev/null; then
    echo "==> Installing system packages from bundle ($DEBS_DIR/*.deb)"
    sudo apt-get install -y --no-download "$DEBS_DIR"/*.deb
else
    echo "==> No debs/ bundle found; assuming apt packages are already installed"
fi
# ------------------------------------------------------------------------------

# Load required-package list from requirements-apt.txt (single source of truth,
# also consumed by prepare_debs.sh). Fall back to the embedded list if the file
# is absent (older checkouts).
REQUIREMENTS_APT="$SCRIPT_DIR/requirements-apt.txt"
required_packages=()
if [ -f "$REQUIREMENTS_APT" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        pkg="${line%%#*}"
        pkg="${pkg// /}"
        pkg="${pkg//$'\t'/}"
        [ -z "$pkg" ] && continue
        required_packages+=("$pkg")
    done < "$REQUIREMENTS_APT"
else
    required_packages=(
        python3-venv
        python3-pip
        python3-dev
        portaudio19-dev
        libportaudio2
        libsndfile1
        ffmpeg
        git
        build-essential
    )
fi

if command -v dpkg >/dev/null 2>&1; then
    for package in "${required_packages[@]}"; do
        if ! dpkg -s "$package" >/dev/null 2>&1; then
            system_missing+=("$package")
        fi
    done

    if [ "${#system_missing[@]}" -gt 0 ]; then
        echo "ERROR: this Pi is still missing required system packages after the debs/ install step:" >&2
        printf '  - %s\n' "${system_missing[@]}" >&2
        cat >&2 <<'EOF'

The debs/ bundle appears incomplete for this Pi.
On the builder Pi (with internet), regenerate the bundle:
    ./prepare_debs.sh
Then re-pull debs/ to the Mac and re-run the fleet deploy.

If this Pi was never meant to receive a debs/ bundle (for example a builder Pi
with internet), install the packages manually with:
    sudo apt-get install -y $(cat requirements-apt.txt 2>/dev/null | sed 's/#.*//' | tr '\n' ' ')

EOF
        exit 1
    fi
fi

echo "==> Prepared bundle looks complete"
echo "==> Installing system and Python dependencies"
SKIP_APT=1 WHEELHOUSE_DIR="$SCRIPT_DIR/wheelhouse" bash "$SCRIPT_DIR/setup_pi.sh"

echo "==> Checking functional microphone config files"
require_path "config_mic1.yaml" "MIC1 config"
require_path "config_mic2.yaml" "MIC2 config"
require_path "config_features.yaml" "feature/log config"
if [ "$missing" -ne 0 ]; then
    exit 1
fi

cat <<'EOF'

Done.

Next steps on this Pi:
  1. source venv/bin/activate
  2. python strip_monitor.py --list-devices
    3. Confirm config_mic1.yaml and config_mic2.yaml match this Pi.
  4. Confirm config_features.yaml matches the experiment feature/log plan.
    5. Start audio processing: ./START_AUDIO_PROCESSING.sh

EOF