#!/usr/bin/env python3
"""Phase 3 helper: install from already-copied bundle on one Pi or fleet targets.

This is an explicit install-only entrypoint. It forwards to
deploy_bundle_to_fleet.py with --install-only.

By default it enables interactive install (SSH TTY) so sudo password prompts
work on fresh Pis.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install from bundle on target Pi(s) (Phase 3 only).")
    parser.add_argument("--host", default="", help="Direct one-off target IP/hostname.")
    parser.add_argument("--hostname", default="", help="Optional display name for --host mode.")
    parser.add_argument("--index", type=int, default=1, help="Optional display index for --host mode.")
    parser.add_argument("--devices-file", default="devices.csv", help="Devices CSV path (default: devices.csv).")
    parser.add_argument("--devices", nargs="+", default=None, metavar="INDEX", help="Target indices, e.g. 1 2 3 or 1-6.")
    parser.add_argument("--user", default="pi", help="SSH username (default: pi).")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22).")
    parser.add_argument("--dest-dir", default="/home/pi/SPEECH_RECORD_ANALYSIS", help="Destination project directory on target Pi(s).")
    parser.add_argument("--non-interactive", action="store_true", help="Disable SSH TTY allocation and run non-interactive install.")
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
        "--install-only",
        "--user",
        args.user,
        "--port",
        str(args.port),
        "--dest-dir",
        args.dest_dir,
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

    if not args.non_interactive:
        cmd.append("--interactive-install")
    if args.dry_run:
        cmd.append("--dry-run")

    print("==> Phase 3: install_from_bundle")
    print(f"$ {shlex.join(cmd)}")
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
