#!/usr/bin/env bash
# Download the system-level .deb packages the fleet Pis need, into ./debs/.
# Role: Deployment Phase 1 helper (build the offline .deb bundle).
# Runs on: One Raspberry Pi with internet access (the "builder" Pi).
# Called by: Manual operator/developer command.
#
# The builder Pi must run the same Raspberry Pi OS release and CPU architecture
# as the fleet Pis. This script reads requirements-apt.txt, downloads every
# listed package plus all transitive dependencies into a repo-local ./debs/
# folder, and stops. It does NOT install anything new on the builder Pi.
#
# The resulting ./debs/ folder is then pulled to the Mac (rsync) and shipped
# to the fleet Pis as part of the bundle. install_from_bundle.sh installs from
# ./debs/ offline via `apt install --no-download ./debs/*.deb`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

REQUIREMENTS="$SCRIPT_DIR/requirements-apt.txt"
DEBS_DIR="$SCRIPT_DIR/debs"

if [ ! -f "$REQUIREMENTS" ]; then
    echo "ERROR: $REQUIREMENTS not found. Cannot decide which .debs to download." >&2
    exit 1
fi

# Parse requirements-apt.txt: strip trailing `# comments` and whitespace.
PACKAGES=()
while IFS= read -r line || [ -n "$line" ]; do
    pkg="${line%%#*}"
    pkg="${pkg// /}"
    pkg="${pkg//$'\t'/}"
    [ -z "$pkg" ] && continue
    PACKAGES+=("$pkg")
done < "$REQUIREMENTS"

if [ "${#PACKAGES[@]}" -eq 0 ]; then
    echo "ERROR: no packages listed in $REQUIREMENTS." >&2
    exit 1
fi

resolve_dep_closure() {
    # Expand hard runtime dependencies recursively.
    # We intentionally exclude recommends/suggests to keep bundle size bounded.
    apt-cache depends --recurse \
        --no-recommends \
        --no-suggests \
        --no-conflicts \
        --no-breaks \
        --no-replaces \
        --no-enhances \
        "$@" \
        | sed -n -E 's/^[[:space:]]*(Pre)?Depends:[[:space:]]*<?([^> ]+)>?/\2/p'
}

is_real_package() {
    local pkg="$1"
    local candidate installed
    candidate="$(apt-cache policy "$pkg" 2>/dev/null | awk '/Candidate:/ {print $2; exit}')"
    installed="$(apt-cache policy "$pkg" 2>/dev/null | awk '/Installed:/ {print $2; exit}')"
    [[ "${candidate:-}" != "(none)" || "${installed:-}" != "(none)" ]]
}

echo "==> Refreshing apt package index (needs sudo)"
sudo apt-get update

echo "==> Clearing existing $DEBS_DIR/*.deb"
mkdir -p "$DEBS_DIR"
sudo rm -f "$DEBS_DIR"/*.deb

echo "==> Downloading .debs (+ every transitive dependency) into $DEBS_DIR"
printf '  - %s\n' "${PACKAGES[@]}"

echo "==> Resolving dependency closure"
mapfile -t RAW_DEPS < <(resolve_dep_closure "${PACKAGES[@]}")

# Include the direct packages explicitly and deduplicate.
ALL_CANDIDATES=("${PACKAGES[@]}" "${RAW_DEPS[@]}")
mapfile -t UNIQUE_CANDIDATES < <(printf '%s\n' "${ALL_CANDIDATES[@]}" | awk 'NF' | sort -u)

RESOLVED_PACKAGES=()
for pkg in "${UNIQUE_CANDIDATES[@]}"; do
    if is_real_package "$pkg"; then
        RESOLVED_PACKAGES+=("$pkg")
    fi
done

if [ "${#RESOLVED_PACKAGES[@]}" -eq 0 ]; then
    echo "ERROR: dependency resolution produced an empty package set." >&2
    exit 1
fi

echo "==> Download set contains ${#RESOLVED_PACKAGES[@]} packages"

# --reinstall forces apt to fetch even packages already installed on the builder.
# --download-only prevents any install step on THIS Pi.
# -o Dir::Cache::archives=... redirects the download cache to our repo-local folder.
sudo apt-get install -y --reinstall --download-only \
    -o Dir::Cache::archives="$DEBS_DIR" \
    "${RESOLVED_PACKAGES[@]}"

# apt writes .debs as root; chown back so rsync/scp as the login user works.
sudo chown -R "$USER" "$DEBS_DIR"

DEB_COUNT="$(find "$DEBS_DIR" -maxdepth 1 -type f -name '*.deb' | wc -l | tr -d ' ')"

cat <<EOF

Done. Staged $DEB_COUNT .deb files in:
    $DEBS_DIR

Next step (on the Mac):
    rsync -avz "$USER@<builder-pi-ip>:$DEBS_DIR/" ./debs/
EOF
