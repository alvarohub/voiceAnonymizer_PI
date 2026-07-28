#!/usr/bin/env python3
"""Check deployment phase status on one Pi or a fleet.

Role: Deployment diagnostics helper.
Runs on: Deployment/control machine.
Called by: Manual command.

Reports per host whether each stage appears complete:
- SYNC (bundle present)
- INSTALL (venv present)
- AUTOCFG (systemd user units enabled)
- AUTORUN (systemd user units active)
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only deployment status check. "
            "Targets devices.csv by default, or one host via --host."
        ),
    )
    parser.add_argument(
        "--host",
        default="",
        help=(
            "Direct one-off target IP or hostname. If set, bypasses devices.csv and "
            "checks exactly this host."
        ),
    )
    parser.add_argument(
        "--hostname",
        default="",
        help=(
            "Optional display name used together with --host for logs and summaries "
            "(default: same value as --host)."
        ),
    )
    parser.add_argument(
        "--index",
        type=int,
        default=1,
        help=(
            "Optional display index used together with --host in logs and summaries "
            "(default: 1)."
        ),
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
        "--project-dir",
        default="/home/pi/SPEECH_RECORD_ANALYSIS",
        help="Project directory on each Pi (default: /home/pi/SPEECH_RECORD_ANALYSIS).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")
    return parser.parse_args()


def _collect_remote_status(device: Device, args: argparse.Namespace) -> tuple[bool, str]:
    remote_script = f"""
proj={shlex.quote(args.project_dir)}
yn() {{
  if [ "$1" -eq 0 ]; then echo yes; else echo no; fi
}}

[ -d "$proj" ]; st=$?; echo project_dir=$(yn $st)
[ -f "$proj/models/iic/emotion2vec_plus_base/model.pt" ]; st=$?; echo model_emotion=$(yn $st)
[ -f "$proj/models/silero-vad/hubconf.py" ]; st=$?; echo model_vad=$(yn $st)
[ -x "$proj/venv/bin/python" ]; st=$?; echo venv_python=$(yn $st)

wheels=$(find "$proj/wheelhouse" -maxdepth 1 -type f -name '*.whl' 2>/dev/null | wc -l | tr -d ' ')
debs=$(find "$proj/debs" -maxdepth 1 -type f -name '*.deb' 2>/dev/null | wc -l | tr -d ' ')
strips=$(pgrep -fc strip_monitor.py 2>/dev/null || true)
echo wheels=$wheels
echo debs=$debs
echo strip_monitor_procs=$strips

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${{XDG_RUNTIME_DIR}}/bus"

for unit in speech-record-mic1.service speech-record-mic2.service; do
  base=$(echo "$unit" | sed 's/\.service$//')
  systemctl --user is-enabled "$unit" >/dev/null 2>&1; st=$?; echo "${{base}}_enabled=$(yn $st)"
  systemctl --user is-active "$unit" >/dev/null 2>&1; st=$?; echo "${{base}}_active=$(yn $st)"
done
"""
    cmd = [
        "ssh",
        "-p",
        str(args.port),
        f"{args.user}@{device.host}",
        "bash -lc " + shlex.quote(remote_script),
    ]
    rendered = shlex.join(cmd)
    print(f"$ {rendered}")
    if args.dry_run:
        return True, ""

    try:
        proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    except FileNotFoundError as exc:
        return False, f"Command not found: {exc}"

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        return False, detail
    return True, proc.stdout


def _parse_kv_lines(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _truth(value: str) -> bool:
    return value.lower() in {"yes", "true", "1", "active", "enabled"}


def _int_or_zero(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _phase_flags(data: dict[str, str]) -> tuple[str, str, str, str]:
    wheels = _int_or_zero(data.get("wheels", "0"))
    debs = _int_or_zero(data.get("debs", "0"))

    sync_ok = (
        _truth(data.get("project_dir", "no"))
        and _truth(data.get("model_emotion", "no"))
        and _truth(data.get("model_vad", "no"))
        and wheels > 0
        and debs > 0
    )
    install_ok = _truth(data.get("venv_python", "no"))

    mic1_enabled = _truth(data.get("speech-record-mic1_enabled", "no"))
    mic2_enabled = _truth(data.get("speech-record-mic2_enabled", "no"))
    mic1_active = _truth(data.get("speech-record-mic1_active", "no"))
    mic2_active = _truth(data.get("speech-record-mic2_active", "no"))

    autocfg_ok = mic1_enabled and mic2_enabled
    autorun_ok = mic1_active and mic2_active

    return (
        "OK" if sync_ok else "NO",
        "OK" if install_ok else "NO",
        "OK" if autocfg_ok else "NO",
        "OK" if autorun_ok else "NO",
    )


def main() -> int:
    args = _parse_args()
    using_direct_host = bool(args.host.strip())
    if using_direct_host and args.devices is not None:
        print("ERROR: --host cannot be combined with --devices. Use one mode or the other.", file=sys.stderr)
        return 2

    if using_direct_host:
        host = args.host.strip()
        hostname = args.hostname.strip() or host
        selected = [Device(index=args.index, hostname=hostname, ip=host)]
        selected_source = f"direct target --host {host}"
    else:
        devices_file = Path(args.devices_file).resolve()
        try:
            all_devices = _load_devices(devices_file)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        all_indices = [d.index for d in all_devices]
        try:
            selected_indices = _parse_indices(args.devices, all_indices)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR parsing --devices: {exc}", file=sys.stderr)
            return 2

        by_index = {d.index: d for d in all_devices}
        selected = []
        for idx in selected_indices:
            device = by_index.get(idx)
            if device is None:
                valid = ", ".join(str(i) for i in all_indices)
                print(f"ERROR: unknown device index {idx}. Known indices: {valid}", file=sys.stderr)
                return 2
            selected.append(device)
        selected_source = str(devices_file)

    if not selected:
        print("ERROR: no devices selected", file=sys.stderr)
        return 2

    print(f"Selected devices from {selected_source}:")
    for device in selected:
        print(f"  - {device.index}: {device.host} ({device.hostname})")
    print("")

    rows: list[tuple[str, str, str, str, str, str, str, str, str, str]] = []
    any_fail = False

    for device in selected:
        ok, payload = _collect_remote_status(device, args)
        if not ok:
            any_fail = True
            rows.append(
                (
                    str(device.index),
                    device.host,
                    "ERR",
                    "ERR",
                    "ERR",
                    "ERR",
                    "-",
                    "-",
                    "-",
                    payload.replace("\n", " ")[:48],
                )
            )
            continue

        data = _parse_kv_lines(payload)
        sync_flag, install_flag, autocfg_flag, autorun_flag = _phase_flags(data)
        rows.append(
            (
                str(device.index),
                device.host,
                sync_flag,
                install_flag,
                autocfg_flag,
                autorun_flag,
                data.get("wheels", "0"),
                data.get("debs", "0"),
                data.get("strip_monitor_procs", "0"),
                "",
            )
        )

    headers = ("IDX", "HOST", "SYNC", "INSTALL", "AUTOCFG", "AUTORUN", "WHEELS", "DEBS", "PROCS", "NOTE")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    def _line(values: tuple[str, ...]) -> str:
        return "  ".join(v.ljust(widths[i]) for i, v in enumerate(values))

    print("")
    print(_line(headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(_line(row))

    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
