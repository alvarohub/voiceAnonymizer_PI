#!/usr/bin/env bash
# Canonical local cleanup helper for testing and demos.
#
# Default behavior clears BOTH local mic processes and local bridge/listeners.
# Use --mics-only or --bridge-only when you need partial cleanup.
#
# Safety notes:
# - PID files are validated against the live process command line before kill.
# - Non-node listeners on bridge ports are reported but not killed by default.
#
# Usage examples:
#   ./fresh_start_local.sh
#   ./fresh_start_local.sh --dry-run
#   ./fresh_start_local.sh --mics-only
#   ./fresh_start_local.sh --bridge-only
#   ./fresh_start_local.sh --bridge-only --force-non-node

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

OSC_PORT="${OSC_PORT:-9000}"
WS_PORT="${WS_PORT:-8765}"
HTTP_PORT="${HTTP_PORT:-3000}"

DRY_RUN=0
FORCE_NON_NODE=0
MODE="all"  # all | mics | bridge

print_usage() {
  cat <<EOF
Usage:
  ./fresh_start_local.sh [--dry-run] [--mics-only|--bridge-only] [--force-non-node]

Default behavior:
  Without scope flags, cleanup includes BOTH local mics and local bridge/listeners.

Options:
  --dry-run         Show what would be killed, but do not kill.
  --mics-only       Stop only local strip_monitor processes.
  --bridge-only     Stop only local bridge/listener processes.
  --force-non-node  Also kill non-node listeners on bridge ports.
  -h, --help        Show this help.

Bridge ports used:
  UDP ${OSC_PORT}, TCP ${WS_PORT}, TCP ${HTTP_PORT}
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --force-non-node)
      FORCE_NON_NODE=1
      shift
      ;;
    --mics-only)
      if [ "$MODE" = "bridge" ]; then
        echo "ERROR: cannot combine --mics-only and --bridge-only" >&2
        exit 1
      fi
      MODE="mics"
      shift
      ;;
    --bridge-only)
      if [ "$MODE" = "mics" ]; then
        echo "ERROR: cannot combine --mics-only and --bridge-only" >&2
        exit 1
      fi
      MODE="bridge"
      shift
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      print_usage >&2
      exit 1
      ;;
  esac
done

ps_cmd() {
  local pid="$1"
  ps -o command= -p "$pid" 2>/dev/null || true
}

ps_comm() {
  local pid="$1"
  ps -o comm= -p "$pid" 2>/dev/null | tr -d ' ' || true
}

is_alive() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

contains_pid() {
  local needle="$1"
  shift
  local p
  for p in "$@"; do
    if [ "$p" = "$needle" ]; then
      return 0
    fi
  done
  return 1
}

terminate_pid() {
  local pid="$1"
  local label="$2"

  if ! is_alive "$pid"; then
    echo "[gone] $label pid=$pid"
    return
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] kill $label pid=$pid"
    return
  fi

  echo "[stop] $label pid=$pid (SIGTERM)"
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3; do
    sleep 1
    if ! is_alive "$pid"; then
      break
    fi
  done
  if is_alive "$pid"; then
    echo "[kill] $label pid=$pid (SIGKILL)"
    kill -9 "$pid" 2>/dev/null || true
  fi
}

validate_pidfile_pid() {
  local pid="$1"
  local expected_pattern="$2"

  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  if ! is_alive "$pid"; then
    return 1
  fi
  local cmd
  cmd="$(ps_cmd "$pid")"
  if [ -z "$cmd" ]; then
    return 1
  fi
  echo "$cmd" | grep -q "$expected_pattern"
}

cleanup_mics() {
  local to_kill=()
  local skipped=()

  # 1) Prefer targeted kills via mic pidfiles, but only if PID still belongs to strip_monitor.
  for tag in mic1 mic2; do
    local pidfile="logs/${tag}.pid"
    if [ ! -f "$pidfile" ]; then
      echo "[skip] $tag: no pidfile"
      continue
    fi

    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if validate_pidfile_pid "$pid" "strip_monitor.py"; then
      local cmd
      cmd="$(ps_cmd "$pid")"
      to_kill+=("$pid|${tag}-pidfile|$cmd")
    else
      local cmd
      cmd="$(ps_cmd "$pid")"
      if [ -n "$cmd" ]; then
        skipped+=("$pid|${tag}-pidfile-reused|$cmd")
      else
        skipped+=("$pid|${tag}-pidfile-stale|process not running")
      fi
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] would remove stale/reused pidfile: $pidfile"
      else
        rm -f "$pidfile"
        echo "[clean] removed stale/reused pidfile: $pidfile"
      fi
    fi
  done

  # 2) Fallback/orphan cleanup: any local strip_monitor process still alive.
  local strip_pids_raw
  strip_pids_raw="$(pgrep -f "strip_monitor.py" 2>/dev/null || true)"
  while IFS= read -r pid; do
    [ -z "$pid" ] && continue
    local cmd
    cmd="$(ps_cmd "$pid")"
    [ -z "$cmd" ] && continue
    to_kill+=("$pid|strip_monitor-orphan|$cmd")
  done <<EOF
$strip_pids_raw
EOF

  # de-duplicate by pid
  local unique=()
  local unique_pids=()
  local row pid
  for row in "${to_kill[@]}"; do
    pid="${row%%|*}"
    if contains_pid "$pid" "${unique_pids[@]-}"; then
      continue
    fi
    unique_pids+=("$pid")
    unique+=("$row")
  done

  if [ "${#unique[@]}" -eq 0 ]; then
    echo "No local strip_monitor processes to stop."
  else
    echo "Mic cleanup targets: ${#unique[@]}"
    for row in "${unique[@]}"; do
      pid="${row%%|*}"
      local rest kind cmd
      rest="${row#*|}"
      kind="${rest%%|*}"
      cmd="${rest#*|}"
      echo "  - pid=$pid  type=$kind  cmd=$cmd"
    done
    for row in "${unique[@]}"; do
      pid="${row%%|*}"
      local rest kind
      rest="${row#*|}"
      kind="${rest%%|*}"
      terminate_pid "$pid" "$kind"
    done
  fi

  if [ "${#skipped[@]}" -gt 0 ]; then
    echo ""
    echo "Skipped PID-file targets for safety (PID reused or stale):"
    for row in "${skipped[@]}"; do
      pid="${row%%|*}"
      local rest kind cmd
      rest="${row#*|}"
      kind="${rest%%|*}"
      cmd="${rest#*|}"
      echo "  - pid=$pid  type=$kind  cmd=$cmd"
    done
  fi
}

cleanup_bridge() {
  local bridge_script_pids_raw
  local bridge_port_pids_raw
  bridge_script_pids_raw="$(pgrep -f "bridge\\.js" 2>/dev/null || true)"
  bridge_port_pids_raw="$({
    lsof -tiUDP:"$OSC_PORT" 2>/dev/null || true
    lsof -tiTCP:"$WS_PORT" -sTCP:LISTEN 2>/dev/null || true
    lsof -tiTCP:"$HTTP_PORT" -sTCP:LISTEN 2>/dev/null || true
  } | awk 'NF' | sort -u)"

  local all_pids
  all_pids="$(printf '%s\n%s\n' "$bridge_script_pids_raw" "$bridge_port_pids_raw" | awk 'NF' | sort -u)"
  if [ -z "$all_pids" ]; then
    echo "No local bridge/listener processes found."
    return
  fi

  # shellcheck disable=SC2206
  local bridge_port_pids=( $bridge_port_pids_raw )
  local to_kill=()
  local skipped=()
  local pid cmd comm

  while IFS= read -r pid; do
    [ -z "$pid" ] && continue
    cmd="$(ps_cmd "$pid")"
    comm="$(ps_comm "$pid")"
    [ -z "$cmd" ] && continue

    if echo "$cmd" | grep -q "bridge.js"; then
      if [ "$comm" = "node" ] || [ "$FORCE_NON_NODE" -eq 1 ]; then
        to_kill+=("$pid|bridge|$cmd")
      else
        skipped+=("$pid|bridge-non-node|$cmd")
      fi
      continue
    fi

    if contains_pid "$pid" "${bridge_port_pids[@]-}"; then
      if [ "$comm" = "node" ] || [ "$FORCE_NON_NODE" -eq 1 ]; then
        to_kill+=("$pid|port-listener|$cmd")
      else
        skipped+=("$pid|port-listener-non-node|$cmd")
      fi
    fi
  done <<EOF
$all_pids
EOF

  if [ "${#to_kill[@]}" -eq 0 ]; then
    echo "No killable local bridge/listener processes found."
  else
    echo "Bridge cleanup targets: ${#to_kill[@]}"
    local row
    for row in "${to_kill[@]}"; do
      pid="${row%%|*}"
      local rest kind
      rest="${row#*|}"
      kind="${rest%%|*}"
      cmd="${rest#*|}"
      echo "  - pid=$pid  type=$kind  cmd=$cmd"
    done
    for row in "${to_kill[@]}"; do
      pid="${row%%|*}"
      local rest kind
      rest="${row#*|}"
      kind="${rest%%|*}"
      terminate_pid "$pid" "$kind"
    done
  fi

  if [ "${#skipped[@]}" -gt 0 ]; then
    echo ""
    echo "Skipped bridge targets for safety (use --force-non-node to kill):"
    local row
    for row in "${skipped[@]}"; do
      pid="${row%%|*}"
      local rest kind
      rest="${row#*|}"
      kind="${rest%%|*}"
      cmd="${rest#*|}"
      echo "  - pid=$pid  type=$kind  cmd=$cmd"
    done
  fi
}

if [ "$MODE" = "all" ] || [ "$MODE" = "mics" ]; then
  cleanup_mics
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "bridge" ]; then
  if [ "$MODE" = "all" ]; then
    echo ""
  fi
  cleanup_bridge
fi

echo ""
if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run complete."
else
  echo "Cleanup complete (scope: $MODE)."
fi
