#!/usr/bin/env python3
"""Phase 2 + 3 deployment helper for the Pi fleet.

Role: Deployment helper for bundle sync and remote install.
Runs on: Deployment/control machine.
Called by: Manual command or deploy_lab_defaults.sh.

Runs from the control machine:
1) optional wheelhouse pull from one connected Pi
2) rsync project folder to selected Pis
3) remote install_from_bundle.sh on selected Pis
"""

from __future__ import annotations

import argparse
import csv
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Device:
    index: int
    hostname: str
    ip: str | None

    @property
    def host(self) -> str:
        return self.ip or self.hostname


def _parse_indices(tokens: list[str] | None, all_indices: list[int]) -> list[int]:
    if not tokens:
        return all_indices

    resolved: list[int] = []
    for token in tokens:
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            resolved.extend(range(start, end + 1) if start <= end else range(start, end - 1, -1))
        else:
            resolved.append(int(token))

    seen: set[int] = set()
    ordered: list[int] = []
    for item in resolved:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _load_devices(path: Path) -> list[Device]:
    if not path.exists():
        raise FileNotFoundError(f"Devices file not found: {path}")

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)

    devices: dict[int, Device] = {}
    for row in rows:
        if "index" not in row or "hostname" not in row:
            raise ValueError("devices.csv must contain columns: index,hostname,ip")

        index = int(str(row.get("index", "")).strip())
        hostname = str(row.get("hostname", "")).strip()
        ip_raw = str(row.get("ip", "")).strip()
        if not hostname:
            raise ValueError(f"Empty hostname for index {index}")

        devices[index] = Device(index=index, hostname=hostname, ip=ip_raw or None)

    if not devices:
        raise ValueError("No devices found in devices file")

    return [devices[idx] for idx in sorted(devices)]


def _run(command: list[str], dry_run: bool) -> tuple[bool, str]:
    rendered = shlex.join(command)
    print(f"$ {rendered}")
    if dry_run:
        return True, ""

    try:
        proc = subprocess.run(command, check=False, text=True, capture_output=True)
    except FileNotFoundError as exc:
        return False, f"Command not found: {exc}"

    if proc.returncode == 0:
        return True, ""

    detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
    return False, detail


def _has_wheels(wheelhouse_dir: Path) -> bool:
    return any(wheelhouse_dir.glob("*.whl"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync bundle and run install_from_bundle.sh on Pi fleet (Phase 2 + 3).",
    )
    parser.add_argument(
        "--devices-file",
        default="devices.csv",
        help="Devices CSV with columns index,hostname,ip (default: devices.csv).",
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        default=None,
        metavar="INDEX",
        help="Optional indices to target, e.g. 1 2 3 or 1-6 (default: all).",
    )
    parser.add_argument("--user", default="pi", help="SSH username (default: pi).")
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22).")
    parser.add_argument(
        "--dest-dir",
        default="/home/pi/SPEECH_RECORD_ANALYSIS",
        help="Destination project directory on each Pi (default: /home/pi/SPEECH_RECORD_ANALYSIS).",
    )
    parser.add_argument(
        "--source-dir",
        default=".",
        help="Local source project directory to sync (default: current directory).",
    )
    parser.add_argument(
        "--pull-wheelhouse",
        default="",
        help=(
            "Optional rsync source for wheelhouse, e.g. "
            "pi@192.168.0.11:/home/pi/SPEECH_RECORD_ANALYSIS/wheelhouse/"
        ),
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Do not pass --delete to rsync.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    source_dir = Path(args.source_dir).resolve()
    devices_file = Path(args.devices_file)
    if not devices_file.is_absolute():
        devices_file = (source_dir / devices_file).resolve()

    try:
        all_devices = _load_devices(devices_file)
    except Exception as exc:  # noqa: BLE001 - user-facing input errors
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    all_indices = [d.index for d in all_devices]
    try:
        selected_indices = _parse_indices(args.devices, all_indices)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR parsing --devices: {exc}", file=sys.stderr)
        return 2

    by_index = {d.index: d for d in all_devices}
    selected: list[Device] = []
    for idx in selected_indices:
        device = by_index.get(idx)
        if device is None:
            valid = ", ".join(str(i) for i in all_indices)
            print(f"ERROR: unknown device index {idx}. Known indices: {valid}", file=sys.stderr)
            return 2
        selected.append(device)

    if not selected:
        print("ERROR: no devices selected", file=sys.stderr)
        return 2

    print(f"Selected devices from {devices_file}:")
    for device in selected:
        print(f"  - {device.index}: {device.host} ({device.hostname})")
    print("")

    wheelhouse_dir = source_dir / "wheelhouse"
    if args.pull_wheelhouse:
        pull_cmd = ["rsync", "-az", args.pull_wheelhouse, str(wheelhouse_dir) + "/"]
        print("==> Pulling wheelhouse")
        ok, detail = _run(pull_cmd, args.dry_run)
        if not ok:
            print(f"ERROR pulling wheelhouse: {detail}", file=sys.stderr)
            return 1
        print("")

    required_local = [
        source_dir / "models" / "iic" / "emotion2vec_plus_base" / "model.pt",
        source_dir / "models" / "silero-vad" / "hubconf.py",
    ]
    missing_local = [str(path) for path in required_local if not path.exists()]
    if missing_local:
        print("ERROR: missing required local bundle files:", file=sys.stderr)
        for path in missing_local:
            print(f"  - {path}", file=sys.stderr)
        return 2

    if not args.dry_run and not _has_wheels(wheelhouse_dir):
        print(
            f"ERROR: no wheel files found in {wheelhouse_dir}. "
            "Run prepare_wheelhouse.sh first or use --pull-wheelhouse.",
            file=sys.stderr,
        )
        return 2

    sync_failed: list[Device] = []
    install_failed: list[Device] = []
    rsync_flags = ["rsync", "-az"]
    if not args.no_delete:
        rsync_flags.append("--delete")
    rsync_flags.extend(
        [
            "--exclude",
            ".git/",
            "--exclude",
            ".venv/",
            "--exclude",
            "venv/",
            "--exclude",
            ".wheelhouse-venv/",
            "--exclude",
            "__pycache__/",
            "--exclude",
            "receiver/node_modules/",
            "--exclude",
            "log_data/",
        ]
    )

    print("==> Phase 2: Sync bundle to Pis")
    for device in selected:
        print(f"[{device.index}:{device.host}] syncing")
        sync_cmd = [
            *rsync_flags,
            str(source_dir) + "/",
            f"{args.user}@{device.host}:{args.dest_dir}/",
        ]
        ok, detail = _run(sync_cmd, args.dry_run)
        if not ok:
            print(f"[{device.index}:{device.host}] SYNC FAILED: {detail}", file=sys.stderr)
            sync_failed.append(device)
    print("")

    print("==> Phase 3: Remote install_from_bundle.sh")
    for device in selected:
        if device in sync_failed:
            print(f"[{device.index}:{device.host}] skipped install (sync failed)")
            install_failed.append(device)
            continue

        print(f"[{device.index}:{device.host}] installing")
        remote = (
            f"cd {shlex.quote(args.dest_dir)} && chmod +x *.sh && bash install_from_bundle.sh"
        )
        ssh_cmd = ["ssh", "-p", str(args.port), f"{args.user}@{device.host}", remote]
        ok, detail = _run(ssh_cmd, args.dry_run)
        if not ok:
            print(f"[{device.index}:{device.host}] INSTALL FAILED: {detail}", file=sys.stderr)
            install_failed.append(device)
    print("")

    sync_ok = [d for d in selected if d not in sync_failed]
    install_ok = [d for d in selected if d not in install_failed]

    def fmt(items: list[Device]) -> str:
        if not items:
            return "none"
        return ", ".join(f"{d.index}:{d.host}" for d in items)

    print("Summary:")
    print(f"  Sync ok: {fmt(sync_ok)}")
    print(f"  Sync failed: {fmt(sync_failed)}")
    print(f"  Install ok: {fmt(install_ok)}")
    print(f"  Install failed: {fmt(install_failed)}")

    return 1 if sync_failed or install_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())