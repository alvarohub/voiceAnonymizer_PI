#!/usr/bin/env python3
"""Discover Pi devices via /hello and fan out one /ctrl command.

Examples:
  python3 broadcast_ctrl.py --list
  python3 broadcast_ctrl.py log_start --expected 12 --timeout 10
    python3 broadcast_ctrl.py log_start --expected 12 --delay-s 3
  python3 broadcast_ctrl.py log_stop --expected 12 --timeout 10
  python3 broadcast_ctrl.py emotion_off --pi 3
  python3 broadcast_ctrl.py vad_on --device 2-1 --device 2-2

Discovery listens on the same UDP port used by /hello (default 9000), so stop
osc_collector.py or receiver/bridge.js first unless you have moved them to a
different port.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime


def _pythonosc_error(exc: ImportError) -> SystemExit:
    print(
        "ERROR: python-osc is required. Install it with: pip install python-osc",
        file=sys.stderr,
    )
    return SystemExit(1)


CTRL_COMMANDS = [
    "osc_start",
    "osc_stop",
    "log_start",
    "log_pause",
    "log_resume",
    "log_stop",
    "query_state",
    "vad_on",
    "vad_off",
    "emotion_on",
    "emotion_off",
    "prosody_on",
    "prosody_off",
]


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    pi_id: str
    mic_id: str
    hostname: str
    ip: str
    ctrl_port: int
    version: str


def _sort_part(value: str):
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _sort_key(device: DeviceInfo):
    return (_sort_part(device.pi_id), _sort_part(device.mic_id), device.device_id)


def _parse_hello(sender_ip: str, args) -> DeviceInfo | None:
    if len(args) >= 6:
        device_id, pi_id, mic_id, hostname, ctrl_port, version = args[:6]
        return DeviceInfo(
            device_id=str(device_id),
            pi_id=str(pi_id),
            mic_id=str(mic_id),
            hostname=str(hostname),
            ip=sender_ip,
            ctrl_port=int(ctrl_port),
            version=str(version),
        )

    if len(args) >= 3:
        pi_id, hostname, version = args[:3]
        return DeviceInfo(
            device_id=f"{pi_id}-1",
            pi_id=str(pi_id),
            mic_id="1",
            hostname=str(hostname),
            ip=sender_ip,
            ctrl_port=9001,
            version=f"{version}(legacy)",
        )

    return None


class Registry:
    def __init__(self):
        self._devices: dict[str, DeviceInfo] = {}
        self._lock = threading.Lock()

    def on_hello(self, client_addr, address, *args):
        sender_ip = client_addr[0] if client_addr else "?"
        device = _parse_hello(sender_ip, args)
        if device is None:
            return

        is_new = False
        with self._lock:
            is_new = device.device_id not in self._devices
            self._devices[device.device_id] = device
            count = len(self._devices)

        if is_new:
            print(
                f"[DISCOVER] {device.device_id:>4s}  {device.hostname:<16s}  "
                f"{device.ip}:{device.ctrl_port}  v{device.version}"
            )

    def snapshot(self) -> list[DeviceInfo]:
        with self._lock:
            devices = list(self._devices.values())
        return sorted(devices, key=_sort_key)


def discover_devices(port: int, timeout: float, expected: int) -> list[DeviceInfo]:
    try:
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import ThreadingOSCUDPServer
    except ImportError as exc:
        raise _pythonosc_error(exc) from exc

    registry = Registry()
    disp = Dispatcher()
    disp.map("/hello", registry.on_hello, needs_reply_address=True)

    try:
        server = ThreadingOSCUDPServer(("0.0.0.0", port), disp)
    except OSError as exc:
        print(
            f"ERROR: cannot bind UDP :{port}. Another process is probably already listening there.",
            file=sys.stderr,
        )
        print(
            "Stop osc_collector.py or receiver/bridge.js first, or move discovery to another port.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="hello-discovery")
    thread.start()
    print(f"[LISTEN ] waiting up to {timeout:g}s on UDP :{port} for /hello heartbeats")

    try:
        time.sleep(timeout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)

    return registry.snapshot()


def select_targets(devices: list[DeviceInfo], wanted_devices: set[str], wanted_pis: set[str]) -> list[DeviceInfo]:
    targets = []
    for device in devices:
        if wanted_devices and device.device_id not in wanted_devices:
            continue
        if wanted_pis and device.pi_id not in wanted_pis:
            continue
        targets.append(device)
    return targets


def print_devices(devices: list[DeviceInfo]):
    if not devices:
        print("[RESULT ] no devices discovered")
        return

    print("")
    print("device_id  hostname          ip:ctrl_port        version")
    print("---------  ----------------  ------------------  -------")
    for device in devices:
        endpoint = f"{device.ip}:{device.ctrl_port}"
        print(f"{device.device_id:>9s}  {device.hostname:<16s}  {endpoint:<18s}  {device.version}")


def command_args(command: str, args) -> list[str]:
    if command != "log_start":
        return []
    now_ms = int(time.time() * 1000)
    if args.start_at_unix_ms is not None:
        target_ms = int(args.start_at_unix_ms)
    else:
        target_ms = now_ms + int(round(float(args.delay_s) * 1000.0))

    target_iso = datetime.utcfromtimestamp(target_ms / 1000.0).isoformat(timespec="milliseconds") + "Z"
    if target_ms > now_ms:
        print(f"[SESSION] scheduled collector_start={target_iso} ({target_ms})")
    else:
        print(f"[SESSION] collector_start={target_iso} ({target_ms})")
    return [str(target_ms), target_iso, str(target_ms), target_iso]


def send_command(command: str, devices: list[DeviceInfo], args) -> None:
    try:
        from pythonosc.udp_client import SimpleUDPClient
    except ImportError as exc:
        raise _pythonosc_error(exc) from exc

    cmd_args = command_args(command, args)

    if command == "osc_start":
        print("[NOTE   ] /ctrl/osc_start routes OSC back to the host running this command")

    for device in devices:
        client = SimpleUDPClient(device.ip, device.ctrl_port)
        client.send_message(f"/ctrl/{command}", cmd_args)
        suffix = f" {cmd_args}" if cmd_args else ""
        print(
            f"[SEND   ] /ctrl/{command}{suffix} -> {device.device_id}  "
            f"({device.hostname} @ {device.ip}:{device.ctrl_port})"
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Discover Pi devices via /hello and fan out a /ctrl command."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=CTRL_COMMANDS,
        help="CTRL command to send to all discovered or filtered devices.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List discovered devices and exit without sending a command.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="How long to listen for /hello heartbeats (default 5s).",
    )
    parser.add_argument(
        "--expected",
        type=int,
        default=0,
        help=(
            "Minimum device count expected after the full discovery window. "
            "Each running mic process is one device. If fewer are found, abort unless "
            "--allow-partial is used."
        ),
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Send the command even if fewer than --expected devices are found.",
    )
    parser.add_argument(
        "--hello-port",
        type=int,
        default=9000,
        help="UDP port used for /hello discovery (default 9000).",
    )
    parser.add_argument(
        "--device",
        action="append",
        default=[],
        help="Target a specific device_id such as 3-1. Repeatable.",
    )
    parser.add_argument(
        "--pi",
        action="append",
        default=[],
        help="Target all devices for one pi_id such as 3. Repeatable.",
    )
    parser.add_argument(
        "--delay-s",
        type=float,
        default=0.0,
        help="For log_start only: schedule the start this many seconds in the future.",
    )
    parser.add_argument(
        "--start-at-unix-ms",
        type=int,
        default=None,
        help="For log_start only: absolute collector timestamp in Unix milliseconds.",
    )
    args = parser.parse_args()

    if not args.list and args.command is None:
        parser.error("either supply a CTRL command or use --list")

    if args.delay_s and args.start_at_unix_ms is not None:
        parser.error("use either --delay-s or --start-at-unix-ms, not both")

    if args.command != "log_start" and (args.delay_s or args.start_at_unix_ms is not None):
        parser.error("--delay-s and --start-at-unix-ms are only valid with log_start")

    return args


def main() -> int:
    args = parse_args()

    devices = discover_devices(args.hello_port, args.timeout, args.expected)
    print_devices(devices)

    if args.expected and len(devices) < args.expected and not args.allow_partial:
        print(
            f"ERROR: expected {args.expected} devices but discovered only {len(devices)}. "
            "Use --allow-partial to proceed anyway.",
            file=sys.stderr,
        )
        return 2

    wanted_devices = {value.strip() for value in args.device if value.strip()}
    wanted_pis = {value.strip() for value in args.pi if value.strip()}
    targets = select_targets(devices, wanted_devices, wanted_pis)

    if args.list:
        return 0

    if not targets:
        print("ERROR: no target devices matched the requested filters.", file=sys.stderr)
        return 1

    print("")
    print(f"[TARGET ] sending /ctrl/{args.command} to {len(targets)} device(s)")
    send_command(args.command, targets, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())