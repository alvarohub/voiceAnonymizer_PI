#!/usr/bin/env python3
"""Install and activate systemd user services for strip_monitor on all Pis.

Role: Deployment Phase 4 helper for autostart services.
Runs on: Deployment/control machine.
Called by: Manual command or deploy_lab_defaults.sh.

This script runs from the control computer. It reads devices from devices.csv
and configures each Pi over SSH:

1) writes one service unit per mic under ~/.config/systemd/user
2) enables linger for the SSH user
3) daemon-reload + enable + restart services

Example:
    python3 configure_auto_start.py
"""

from __future__ import annotations

import argparse
import csv
import shlex
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable


@dataclass
class PiConfig:
    index: int
    hostname: str
    ip: str | None
    mic_ids: set[str] = field(default_factory=set)


def _sort_part(value: str):
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _sorted_mics(mic_ids: Iterable[str]) -> list[str]:
    return sorted({str(m) for m in mic_ids}, key=_sort_part)


def _parse_indices(tokens: list[str] | None, all_indices: list[int]) -> list[int]:
    """Resolve --devices tokens (single values or ranges) to ordered unique indices."""
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
    for index in resolved:
        if index not in seen:
            seen.add(index)
            ordered.append(index)
    return ordered


def _load_devices(path: str) -> list[PiConfig]:
    devices_file = Path(path)
    if not devices_file.exists():
        raise FileNotFoundError(
            f"Devices file not found: {devices_file}. Create a devices.csv with columns index,hostname,ip."
        )

    rows: list[dict[str, str]] = []
    with devices_file.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)

    by_index: dict[int, PiConfig] = {}
    for row in rows:
        if "index" not in row or "hostname" not in row:
            raise ValueError("devices.csv must contain at least these columns: index,hostname,ip")

        idx = int(str(row.get("index", "")).strip())
        hostname = str(row.get("hostname", "")).strip()
        ip_raw = str(row.get("ip", "")).strip()
        if not hostname:
            raise ValueError(f"Row with index {idx} has empty hostname in {devices_file}")

        by_index[idx] = PiConfig(
            index=idx,
            hostname=hostname,
            ip=ip_raw or None,
            mic_ids={"1", "2"},
        )

    if not by_index:
        raise ValueError(f"No devices found in {devices_file}")

    return [by_index[idx] for idx in sorted(by_index)]


def _unit_name(mic_id: str) -> str:
    return f"speech-record-mic{mic_id}.service"


def _build_unit(project_dir: str, mic_id: str, restart_sec: int) -> str:
    config = f"config_mic{mic_id}.yaml"
    exec_start = (
        f"/bin/bash -lc 'source {project_dir}/venv/bin/activate && "
        f"python3 -u strip_monitor.py --config {config} --features-config config_features.yaml'"
    )
    return "\n".join(
        [
            "[Unit]",
            f"Description=Speech Record Analysis MIC{mic_id}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={project_dir}",
            f"ExecStart={exec_start}",
            "Restart=always",
            f"RestartSec={restart_sec}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _connect_ssh(host: str, user: str, password: str | None, port: int):
    try:
        import paramiko  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("paramiko is required. Install with: pip install paramiko") from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password if password else None,
        timeout=10,
        look_for_keys=True,
        allow_agent=True,
    )
    return client


def _run_remote(ssh, command: str, check: bool = True, stdin_text: str | None = None) -> tuple[int, str, str]:
    stdin, stdout, stderr = ssh.exec_command(command)
    if stdin_text:
        stdin.write(stdin_text)
        stdin.flush()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if check and code != 0:
        raise RuntimeError(f"Remote command failed ({code}): {command}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    return code, out, err


def _remote_bash(
    ssh,
    host: str,
    user: str,
    script: str,
    dry_run: bool,
    log: Callable[[str], None],
) -> None:
    cmd = "bash -lc " + shlex.quote(script)
    log(f"$ ssh {user}@{host} {shlex.quote(cmd)}")
    if dry_run:
        return
    _run_remote(ssh, cmd)


def _user_systemctl(
    ssh,
    host: str,
    user: str,
    args: str,
    dry_run: bool,
    log: Callable[[str], None],
) -> None:
    prefix = (
        'export XDG_RUNTIME_DIR="/run/user/$(id -u)"; '
        'export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"; '
    )
    _remote_bash(ssh, host, user, prefix + f"systemctl --user {args}", dry_run, log)


def _enable_linger(
    ssh,
    host: str,
    user: str,
    password: str,
    dry_run: bool,
    log: Callable[[str], None],
) -> None:
    cmd_no_prompt = f"sudo -n loginctl enable-linger {shlex.quote(user)}"
    log(f"$ ssh {user}@{host} {shlex.quote(cmd_no_prompt)}")
    if dry_run:
        return

    code, _, _ = _run_remote(ssh, cmd_no_prompt, check=False)
    if code == 0:
        return

    if not password:
        raise RuntimeError(
            "enable-linger needs sudo password or passwordless sudo. "
            "Provide --password or configure passwordless sudo for loginctl."
        )

    cmd_with_password = f"sudo -S -p '' loginctl enable-linger {shlex.quote(user)}"
    log(f"$ ssh {user}@{host} {shlex.quote(cmd_with_password)}")
    _run_remote(ssh, cmd_with_password, check=True, stdin_text=password + "\n")


def _write_remote_file(
    ssh,
    host: str,
    user: str,
    path: str,
    content: str,
    dry_run: bool,
    log: Callable[[str], None],
) -> None:
    log(f"$ sftp {user}@{host}:{path} (write unit)")
    if dry_run:
        return
    with ssh.open_sftp() as sftp:
        with sftp.open(path, "w") as handle:
            handle.write(content)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install and activate speech-record systemd user services on Pis listed in devices.csv.",
    )
    parser.add_argument(
        "--devices-file",
        default="devices.csv",
        help="CSV file with devices (columns: index,hostname,ip). Default: devices.csv.",
    )
    parser.add_argument(
        "--devices",
        nargs="+",
        default=None,
        metavar="INDEX",
        help="Optional device indices to configure, e.g. 1 2 3 or 1-6. Defaults to all devices.",
    )
    parser.add_argument("--user", default="pi", help="SSH username (default: pi).")
    parser.add_argument(
        "--password",
        default="",
        help="SSH/sudo password. Optional if SSH keys and passwordless sudo are configured.",
    )
    parser.add_argument("--port", type=int, default=22, help="SSH port (default: 22).")
    parser.add_argument(
        "--project-dir",
        default="/home/pi/SPEECH_RECORD_ANALYSIS",
        help="Project directory on each Pi (default: /home/pi/SPEECH_RECORD_ANALYSIS).",
    )
    parser.add_argument(
        "--restart-sec",
        type=int,
        default=2,
        help="RestartSec value in units (default: 2).",
    )
    parser.add_argument(
        "--skip-linger",
        action="store_true",
        help="Skip loginctl enable-linger step.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing.")
    return parser.parse_args()


def _configure_one(pi: PiConfig, args: argparse.Namespace) -> tuple[bool, list[str]]:
    host = pi.ip or pi.hostname
    mics = _sorted_mics(pi.mic_ids)
    unit_names = [_unit_name(m) for m in mics]

    lines: list[str] = []

    def log(message: str) -> None:
        lines.append(f"[{pi.index}:{host}] {message}")

    log(f"Configuring hostname={pi.hostname} ip={pi.ip or '-'} mics={','.join(mics)}")

    ssh = None
    try:
        if not args.dry_run:
            ssh = _connect_ssh(host, args.user, args.password, args.port)

        unit_dir = "~/.config/systemd/user"
        _remote_bash(ssh, host, args.user, f"mkdir -p {unit_dir}", args.dry_run, log)

        for mic_id in mics:
            unit_name = _unit_name(mic_id)
            unit_path = f"{unit_dir}/{unit_name}"
            unit_content = _build_unit(args.project_dir, mic_id, args.restart_sec)
            _write_remote_file(ssh, host, args.user, unit_path, unit_content, args.dry_run, log)

        if not args.skip_linger:
            _enable_linger(ssh, host, args.user, args.password, args.dry_run, log)

        _user_systemctl(ssh, host, args.user, "daemon-reload", args.dry_run, log)
        _user_systemctl(
            ssh,
            host,
            args.user,
            "enable " + " ".join(unit_names),
            args.dry_run,
            log,
        )
        _user_systemctl(
            ssh,
            host,
            args.user,
            "restart " + " ".join(unit_names),
            args.dry_run,
            log,
        )
        _user_systemctl(
            ssh,
            host,
            args.user,
            "is-active " + " ".join(unit_names),
            args.dry_run,
            log,
        )
        log("Configured. Services are active and enabled.")
        return True, lines
    except Exception as exc:  # noqa: BLE001 - keep other hosts running.
        log(f"FAILED: {exc}")
        return False, lines
    finally:
        if ssh is not None:
            ssh.close()


def main() -> int:
    args = _parse_args()

    all_devices = _load_devices(args.devices_file)
    all_indices = [d.index for d in all_devices]
    selected_indices = _parse_indices(args.devices, all_indices)
    by_index = {d.index: d for d in all_devices}

    pis: list[PiConfig] = []
    for index in selected_indices:
        item = by_index.get(index)
        if item is None:
            valid = ", ".join(str(i) for i in all_indices)
            print(f"Unknown device index {index}. Known indices: {valid}", file=sys.stderr)
            return 2
        pis.append(item)

    if not pis:
        print("No matching devices found.", file=sys.stderr)
        return 2

    summary = ", ".join(f"{p.index}:{(p.ip or p.hostname)}" for p in pis)
    print(f"Configuring {len(pis)} device(s) from {args.devices_file}: {summary}")
    print("")

    print_lock = Lock()
    results: dict[int, bool] = {}

    with ThreadPoolExecutor(max_workers=len(pis)) as executor:
        futures = {executor.submit(_configure_one, pi, args): pi.index for pi in pis}

        for future in as_completed(futures):
            index = futures[future]
            ok, lines = future.result()
            results[index] = ok
            with print_lock:
                print("\n".join(lines))
                print("")

    succeeded = sorted(index for index, ok in results.items() if ok)
    failed = sorted(index for index, ok in results.items() if not ok)

    print(f"Done. Succeeded: {succeeded or 'none'}. Failed: {failed or 'none'}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
