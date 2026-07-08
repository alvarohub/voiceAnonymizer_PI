#!/usr/bin/env python3
"""Discover Pi devices via /hello and fan out one /ctrl command.

Examples:
  python3 broadcast_ctrl.py --list
    python3 broadcast_ctrl.py status --expected 12 --timeout 10
    python3 broadcast_ctrl.py log_start --expected 12 --timeout 10 --delay-s 3
    python3 broadcast_ctrl.py log_start --pi 4 --mic 1
    python3 broadcast_ctrl.py log_save_stop --expected 12 --timeout 10
  python3 broadcast_ctrl.py emotion_off --pi 3
  python3 broadcast_ctrl.py vad_on --device 2-1 --device 2-2

Discovery listens on the same UDP port used by /hello (default 9000), so stop
osc_collector.py or receiver/bridge.js first unless you have moved them to a
different port.
"""

from __future__ import annotations

import argparse
import re
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
    "status",
    "osc_start",
    "osc_stop",
    "log_start",
    "log_pause",
    "log_resume",
    "log_stop",
    "log_save_stop",
    "log_discard_stop",
    "query_state",
    "audio_reconnect",
    "vad_on",
    "vad_off",
    "emotion_on",
    "emotion_off",
    "prosody_on",
    "prosody_off",
]

ACK_SENTINEL = "__ack__"
DEFAULT_ACK_TIMEOUT_MS = 150


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    pi_id: str
    mic_id: str
    hostname: str
    ip: str
    ctrl_port: int
    version: str


@dataclass(frozen=True)
class PendingCommand:
    device: DeviceInfo
    command: str
    cmd_id: str
    sent_at: float


@dataclass(frozen=True)
class AckRecord:
    device_id: str
    command: str
    cmd_id: str
    ok: bool
    message: str
    received_at: float


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


def select_targets(devices: list[DeviceInfo], wanted_devices: set[str],
                   wanted_pis: set[str], wanted_mics: set[str]) -> list[DeviceInfo]:
    targets = []
    for device in devices:
        if wanted_devices and device.device_id not in wanted_devices:
            continue
        if wanted_pis and device.pi_id not in wanted_pis:
            continue
        if wanted_mics and device.mic_id not in wanted_mics:
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
    command = actual_command(command)
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


def actual_command(command: str) -> str:
    return "query_state" if command == "status" else command


class AckRegistry:
    def __init__(self, expected: dict[str, PendingCommand]):
        self.expected = expected
        self.records: dict[str, AckRecord] = {}
        self._cond = threading.Condition()

    def on_ack(self, client_addr, address, *args):
        match = re.match(r"^/dev/([^/]+)/ack$", address)
        if not match or len(args) < 3:
            return
        device_id = match.group(1)
        command = str(args[0])
        cmd_id = str(args[1])
        if cmd_id not in self.expected:
            print(f"[ACK ? ] {device_id:<8s} {command:<12s} unexpected cmd_id={cmd_id}")
            return
        ok_raw = args[2]
        ok = ok_raw is True or ok_raw == 1 or ok_raw == "1" or str(ok_raw).lower() == "true"
        message = str(args[3]) if len(args) >= 4 else "ok"
        with self._cond:
            self.records[cmd_id] = AckRecord(
                device_id=device_id,
                command=command,
                cmd_id=cmd_id,
                ok=ok,
                message=message,
                received_at=time.time(),
            )
            self._cond.notify_all()

    def wait(self, timeout_s: float):
        deadline = time.time() + timeout_s
        with self._cond:
            while len(self.records) < len(self.expected):
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
            return dict(self.records)


def send_command(command: str, devices: list[DeviceInfo], args) -> bool:
    try:
        from pythonosc.udp_client import SimpleUDPClient
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import ThreadingOSCUDPServer
    except ImportError as exc:
        raise _pythonosc_error(exc) from exc

    send_name = actual_command(command)
    cmd_args = command_args(send_name, args)
    pending: dict[str, PendingCommand] = {}

    ack_server = None
    ack_thread = None
    ack_port = None
    ack_registry = None
    if not args.no_ack:
        for index, device in enumerate(devices, 1):
            cmd_id = f"{send_name}-{device.device_id}-{int(time.time() * 1000)}-{index}"
            pending[cmd_id] = PendingCommand(
                device=device,
                command=send_name,
                cmd_id=cmd_id,
                sent_at=0.0,
            )
        ack_registry = AckRegistry(pending)
        disp = Dispatcher()
        for device in devices:
            disp.map(f"/dev/{device.device_id}/ack", ack_registry.on_ack, needs_reply_address=True)
        ack_server = ThreadingOSCUDPServer(("0.0.0.0", int(args.ack_port)), disp)
        ack_port = int(ack_server.server_address[1])
        ack_thread = threading.Thread(target=ack_server.serve_forever, daemon=True, name="ctrl-ack")
        ack_thread.start()

    if send_name == "osc_start":
        print("[NOTE   ] /ctrl/osc_start routes OSC back to the host running this command")

    pending_ids = list(pending.keys())
    for index, device in enumerate(devices, 1):
        args_to_send = list(cmd_args)
        cmd_id = None
        if not args.no_ack:
            cmd_id = pending_ids[index - 1]
            pending[cmd_id] = PendingCommand(
                device=device,
                command=send_name,
                cmd_id=cmd_id,
                sent_at=time.time(),
            )
            args_to_send.extend([ACK_SENTINEL, cmd_id, str(ack_port)])
        client = SimpleUDPClient(device.ip, device.ctrl_port)
        client.send_message(f"/ctrl/{send_name}", args_to_send)
        suffix = f" {cmd_args}" if cmd_args else ""
        ack_note = f" ack={cmd_id}" if cmd_id else ""
        print(
            f"[SEND   ] /ctrl/{send_name}{suffix}{ack_note} -> {device.device_id}  "
            f"({device.hostname} @ {device.ip}:{device.ctrl_port})"
        )

    if args.no_ack:
        return True

    assert ack_registry is not None
    records = ack_registry.wait(args.ack_timeout_ms / 1000.0)
    if ack_server is not None:
        ack_server.shutdown()
        ack_server.server_close()
    if ack_thread is not None:
        ack_thread.join(timeout=1.0)

    all_ok = True
    print("")
    for cmd_id, item in pending.items():
        record = records.get(cmd_id)
        if record is None:
            all_ok = False
            print(
                f"[ACK TO ] {item.device.device_id:<8s} {item.command:<12s} "
                f"no reply after {args.ack_timeout_ms}ms"
            )
            continue
        elapsed_ms = int(round((record.received_at - item.sent_at) * 1000.0))
        status = "OK" if record.ok else "ERR"
        if not record.ok:
            all_ok = False
        if command == "status":
            print(f"[STATUS ] {item.device.device_id:<8s} {record.message} ({elapsed_ms}ms)")
        else:
            print(
                f"[ACK {status:<3s}] {item.device.device_id:<8s} {record.command:<12s} "
                f"{elapsed_ms}ms  {record.message}"
            )

    return all_ok


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
        "--mic",
        action="append",
        default=[],
        help="Target all devices for one mic_id such as 1. Repeatable.",
    )
    parser.add_argument(
        "--ack-timeout-ms",
        type=int,
        default=DEFAULT_ACK_TIMEOUT_MS,
        help=f"How long to wait for command ACKs (default {DEFAULT_ACK_TIMEOUT_MS}ms).",
    )
    parser.add_argument(
        "--ack-port",
        type=int,
        default=0,
        help="Local UDP port for command ACK replies (default 0 = choose automatically).",
    )
    parser.add_argument(
        "--no-ack",
        action="store_true",
        help="Send commands without waiting for command acknowledgements.",
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
    wanted_mics = {value.strip() for value in args.mic if value.strip()}
    targets = select_targets(devices, wanted_devices, wanted_pis, wanted_mics)

    if args.list:
        return 0

    if not targets:
        print("ERROR: no target devices matched the requested filters.", file=sys.stderr)
        return 1

    print("")
    print(f"[TARGET ] sending /ctrl/{actual_command(args.command)} to {len(targets)} device(s)")
    return 0 if send_command(args.command, targets, args) else 3


if __name__ == "__main__":
    raise SystemExit(main())