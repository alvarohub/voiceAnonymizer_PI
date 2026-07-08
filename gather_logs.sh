#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./gather_logs.sh [options] [destination_dir] [host1 host2 ...]

Options:
  --remote-path PATH   Remote log-data path relative to the Pi user's home.
                       Default: SPEECH_RECORD_ANALYSIS/log_data/
  --remove-source      Remove remote files after successful transfer.
  -h, --help           Show this help.

Examples:
  ./gather_logs.sh
  ./gather_logs.sh log_data/session_20260524
  ./gather_logs.sh --remove-source log_data/session_20260524 emotionpi1 emotionpi2
EOF
}

REMOTE_PATH="SPEECH_RECORD_ANALYSIS/log_data/"
REMOVE_SOURCE=0

while (($# > 0)); do
  case "$1" in
    --remote-path)
      REMOTE_PATH="$2"
      shift 2
      ;;
    --remove-source)
      REMOVE_SOURCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -* )
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

DEST_ROOT="log_data/gathered/$(date +%Y%m%d_%H%M%S)"
if (($# > 0)) && [[ "$1" == */* || "$1" == log_data* || "$1" == .* ]]; then
  DEST_ROOT="$1"
  shift
fi

if (($# > 0)); then
  HOSTS=("$@")
else
  HOSTS=(emotionpi1 emotionpi2 emotionpi3 emotionpi4 emotionpi5 emotionpi6)
fi

mkdir -p "$DEST_ROOT"

RSYNC_ARGS=(-avz)
if ((REMOVE_SOURCE)); then
  RSYNC_ARGS+=(--remove-source-files)
fi

for host in "${HOSTS[@]}"; do
  echo "[GATHER] $host -> $DEST_ROOT/$host"
  mkdir -p "$DEST_ROOT/$host"
  rsync "${RSYNC_ARGS[@]}" \
    "$host:$REMOTE_PATH" \
    "$DEST_ROOT/$host/"
done

echo "[DONE] gathered logs into $DEST_ROOT"