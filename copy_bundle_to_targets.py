#!/usr/bin/env python3
"""Phase 2 helper: copy bundle to one Pi or fleet targets.

This is an explicit copy-only entrypoint. It forwards to
deploy_bundle_to_fleet.py with --sync-only so operators do not need to manage
phase-combination flags directly.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy bundle to target Pi(s) (Phase 2 only).")
    parser.add_argument("--host", default="", help="Direct one-off target IP/hostname.")
    parser.add_argument("--hostname", default="", help="Optional display name for --host mode.")
    parser.add_argument("--index", type=int, default=1, help="Optional display index for --host mode.")
    parser.add_argument("--devices-file", default="devices.csv", help="Devices CSV path (default: devices.csv).")
    parser.add_argument("--devices", nargs="+", default=None, metavar="INDEX", help="Target indices, e.g. 1 2 3 or 1-6.")
    parser.add_argument("--user", default="pi", help="SSH username (default: pi).")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22).")
    parser.add_argument("--dest-dir", default="/home/pi/SPEECH_RECORD_ANALYSIS", help="Destination project directory on target Pi(s).")
    parser.add_argument("--source-dir", default=".", help="Local source project directory (default: current directory).")
    parser.add_argument("--pull-wheelhouse", default="", help="Optional rsync source for wheelhouse before sync.")
    parser.add_argument("--no-delete", action="store_true", help="Do not pass --delete to rsync.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.host and args.devices is not None:
        print("ERROR: --host cannot be combined with --devices.", file=sys.stderr)
        return 2

    cmd = [
        "python3",
        "deploy_bundle_to_fleet.py",
        "--sync-only",
        "--user",
        args.user,
        "--port",
        str(args.port),
        "--dest-dir",
        args.dest_dir,
        "--source-dir",
        args.source_dir,
        "--devices-file",
        args.devices_file,
    ]
    if args.host:
        cmd.extend(["--host", args.host])
        if args.hostname:
            cmd.extend(["--hostname", args.hostname])
        if args.index != 1:
            cmd.extend(["--index", str(args.index)])
    elif args.devices:
        cmd.extend(["--devices", *args.devices])

    if args.pull_wheelhouse:
        cmd.extend(["--pull-wheelhouse", args.pull_wheelhouse])
    if args.no_delete:
        cmd.append("--no-delete")
    if args.dry_run:
        cmd.append("--dry-run")

    print("==> Phase 2: copy_bundle")
    print(f"$ {shlex.join(cmd)}")
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
