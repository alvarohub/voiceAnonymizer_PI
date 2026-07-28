#!/usr/bin/env python3
"""Phase 4 helper: configure autostart services on one Pi or fleet targets.

This is an explicit autostart-only entrypoint. It forwards to
configure_auto_start.py with the same target-selection options.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure autostart services on target Pi(s) (Phase 4 only).")
    parser.add_argument("--host", default="", help="Direct one-off target IP/hostname.")
    parser.add_argument("--hostname", default="", help="Optional display name for --host mode.")
    parser.add_argument("--index", type=int, default=1, help="Optional display index for --host mode.")
    parser.add_argument("--devices-file", default="devices.csv", help="Devices CSV path (default: devices.csv).")
    parser.add_argument("--devices", nargs="+", default=None, metavar="INDEX", help="Target indices, e.g. 1 2 3 or 1-6.")
    parser.add_argument("--user", default="pi", help="SSH username (default: pi).")
    parser.add_argument("--password", default="", help="Optional SSH/sudo password.")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22).")
    parser.add_argument("--project-dir", default="/home/pi/SPEECH_RECORD_ANALYSIS", help="Project directory on target Pi(s).")
    parser.add_argument("--restart-sec", type=int, default=2, help="Service RestartSec value (default: 2).")
    parser.add_argument("--skip-linger", action="store_true", help="Skip loginctl enable-linger.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.host and args.devices is not None:
        print("ERROR: --host cannot be combined with --devices.", file=sys.stderr)
        return 2

    cmd = [
        "python3",
        "configure_auto_start.py",
        "--user",
        args.user,
        "--port",
        str(args.port),
        "--project-dir",
        args.project_dir,
        "--restart-sec",
        str(args.restart_sec),
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

    if args.password:
        cmd.extend(["--password", args.password])
    if args.skip_linger:
        cmd.append("--skip-linger")
    if args.dry_run:
        cmd.append("--dry-run")

    print("==> Phase 4: autostart_config")
    print(f"$ {shlex.join(cmd)}")
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
