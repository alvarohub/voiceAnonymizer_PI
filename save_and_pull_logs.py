#!/usr/bin/env python3
"""Broadcast log_save_stop, collect per-file save notices, then pull files over SSH.

Typical usage:
  python save_and_pull_logs.py --session start_recording_session.yaml
  python save_and_pull_logs.py --session start_recording_session.yaml take_001
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from speech_control import ACK_SENTINEL, Target, load_bridge_targets, load_session_plan


@dataclass(frozen=True)
class PendingSave:
    target: Target
    cmd_id: str
    sent_at: float


@dataclass(frozen=True)
class SaveAck:
    device_id: str
    cmd_id: str
    ok: bool
    message: str
    received_at: float


@dataclass(frozen=True)
class SavedFileNotice:
    device_id: str
    cmd_id: str
    kind: str
    filename: str
    remote_path: str
    received_at: float


class SaveAckCollector:
    def __init__(self, pending_by_cmd: dict[str, PendingSave]):
        self.pending_by_cmd = pending_by_cmd
        self.final_acks: dict[str, SaveAck] = {}
        self.saved_notices: list[SavedFileNotice] = []
        self._cond = threading.Condition()

    def on_ack(self, client_addr, address, *args):
        match = re.match(r"^/dev/([^/]+)/ack$", address)
        if not match or len(args) < 3:
            return
        device_id = match.group(1)
        command = str(args[0])
        cmd_id = str(args[1])
        if cmd_id not in self.pending_by_cmd:
            return
        if command != "log_save_stop":
            return

        ok_raw = args[2]
        ok = ok_raw is True or ok_raw == 1 or ok_raw == "1" or str(ok_raw).lower() == "true"
        message = str(args[3]) if len(args) >= 4 else ("ok" if ok else "error")

        with self._cond:
            self.final_acks[cmd_id] = SaveAck(
                device_id=device_id,
                cmd_id=cmd_id,
                ok=ok,
                message=message,
                received_at=time.time(),
            )
            self._cond.notify_all()

    def on_saved(self, client_addr, address, *args):
        match = re.match(r"^/dev/([^/]+)/saved$", address)
        if not match or len(args) < 4:
            return
        device_id = match.group(1)
        cmd_id = str(args[0])
        if cmd_id not in self.pending_by_cmd:
            return

        notice = SavedFileNotice(
            device_id=device_id,
            cmd_id=cmd_id,
            kind=str(args[1]),
            filename=str(args[2]),
            remote_path=str(args[3]),
            received_at=time.time(),
        )
        with self._cond:
            self.saved_notices.append(notice)
            self._cond.notify_all()

    def wait_for_final_acks(self, timeout_s: float) -> tuple[dict[str, SaveAck], list[SavedFileNotice]]:
        deadline = time.time() + timeout_s
        with self._cond:
            while len(self.final_acks) < len(self.pending_by_cmd):
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
            return dict(self.final_acks), list(self.saved_notices)


def _remote_spec(user: str, ip: str, remote_path: str) -> str:
    # scp routes this through a remote shell, so quote single quotes safely.
    escaped = remote_path.replace("'", "'\"'\"'")
    return f"{user}@{ip}:'{escaped}'"


def _discover_targets(args) -> list[Target]:
    if args.session:
        return load_session_plan(args.session).targets

    targets = load_bridge_targets(args.bridge)
    if args.all:
        return targets
    return [t for t in targets if t.audio_ok is True]


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description="Broadcast save, collect per-file save notices, and pull logs over SSH."
    )
    parser.add_argument("save_name", nargs="?", help="Optional base save filename (without suffixes)")
    parser.add_argument("--session", default="start_recording_session.yaml",
                        help="Session YAML for exact Pi/mic target list (default: start_recording_session.yaml)")
    parser.add_argument("--bridge", default="http://localhost:3000",
                        help="Bridge URL used only if --session is empty")
    parser.add_argument("--all", action="store_true",
                        help="In bridge mode, include audio-failure processes too")
    parser.add_argument("--ack-timeout", type=float, default=120.0,
                        help="Seconds to wait for final save ACKs (default: 120)")
    parser.add_argument("--ack-port", type=int, default=0,
                        help="Local UDP port for save ACK collection (default: 0 = auto)")
    parser.add_argument("--ssh-user", default="pi", help="SSH username for scp pull (default: pi)")
    parser.add_argument("--dest-dir", default=None,
                        help="Destination folder (default: log_data/pulled/<timestamp>)")
    parser.add_argument("--scp-timeout", type=float, default=60.0,
                        help="Per-file scp timeout in seconds (default: 60)")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without sending or copying")
    parsed = parser.parse_args(argv)
    if parsed.ack_timeout <= 0:
        parser.error("--ack-timeout must be > 0")
    if parsed.scp_timeout <= 0:
        parser.error("--scp-timeout must be > 0")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        targets = _discover_targets(args)
    except Exception as exc:
        print(f"ERROR: failed to discover targets: {exc}", file=sys.stderr)
        return 1

    if not targets:
        print("ERROR: no targets found", file=sys.stderr)
        return 2

    targets = sorted(targets, key=lambda t: (t.pi_id, t.mic_id, t.device_id))
    target_by_device = {t.device_id: t for t in targets}

    pending: dict[str, PendingSave] = {}
    for idx, target in enumerate(targets, 1):
        cmd_id = f"save-{target.device_id}-{int(time.time() * 1000)}-{idx}-{uuid.uuid4().hex[:6]}"
        pending[cmd_id] = PendingSave(target=target, cmd_id=cmd_id, sent_at=0.0)

    if args.dest_dir:
        dest_root = Path(args.dest_dir)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_root = Path("log_data") / "pulled" / stamp

    print(f"[TARGET ] {len(targets)} process(es)")
    for target in targets:
        print(f"  - {target.device_id:>8s}  {target.ip}:{target.ctrl_port}")

    if args.dry_run:
        print("[DRYRUN ] would send /ctrl/log_save_stop to all targets and pull files from /dev/*/saved notices")
        return 0

    try:
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import ThreadingOSCUDPServer
        from pythonosc.udp_client import SimpleUDPClient
    except ImportError:
        print("ERROR: python-osc is required. Install with: pip install python-osc", file=sys.stderr)
        return 1

    collector = SaveAckCollector(pending)
    disp = Dispatcher()
    for target in targets:
        disp.map(f"/dev/{target.device_id}/ack", collector.on_ack, needs_reply_address=True)
        disp.map(f"/dev/{target.device_id}/saved", collector.on_saved, needs_reply_address=True)

    server = ThreadingOSCUDPServer(("0.0.0.0", int(args.ack_port)), disp)
    ack_port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="save-ack-listener")
    thread.start()

    print(f"[LISTEN ] save ACKs on UDP :{ack_port}")

    for idx, target in enumerate(targets, 1):
        cmd_id = list(pending.keys())[idx - 1]
        pending[cmd_id] = PendingSave(target=target, cmd_id=cmd_id, sent_at=time.time())
        send_args = []
        if args.save_name:
            send_args.append(str(args.save_name))
        send_args.extend([ACK_SENTINEL, cmd_id, str(ack_port)])
        client = SimpleUDPClient(target.ip, target.ctrl_port)
        client.send_message("/ctrl/log_save_stop", send_args)
        print(f"[SEND   ] /ctrl/log_save_stop -> {target.device_id}  ack={cmd_id}")

    final_acks, saved_notices = collector.wait_for_final_acks(args.ack_timeout)
    server.shutdown()
    server.server_close()
    thread.join(timeout=1.0)

    print("\n[ACKS   ]")
    all_ok = True
    for cmd_id, item in pending.items():
        ack = final_acks.get(cmd_id)
        if ack is None:
            all_ok = False
            print(f"  TIMEOUT  {item.target.device_id:>8s}  no final log_save_stop ACK")
            continue
        elapsed_ms = int(round((ack.received_at - item.sent_at) * 1000.0))
        status = "OK" if ack.ok else "ERR"
        if not ack.ok:
            all_ok = False
        print(f"  {status:<7s} {item.target.device_id:>8s}  {elapsed_ms:5d}ms  {ack.message}")

    notices_sorted = sorted(saved_notices, key=lambda n: (n.device_id, n.kind, n.filename))
    if not notices_sorted:
        print("\n[FILES  ] no /saved notices received")
        return 3 if not all_ok else 4

    print("\n[FILES  ]")
    unique_notices: list[SavedFileNotice] = []
    seen = set()
    for notice in notices_sorted:
        key = (notice.device_id, notice.remote_path)
        if key in seen:
            continue
        seen.add(key)
        unique_notices.append(notice)
        print(f"  {notice.device_id:>8s}  {notice.kind:<9s}  {notice.remote_path}")

    failures = 0
    for notice in unique_notices:
        target = target_by_device.get(notice.device_id)
        if target is None:
            print(f"[PULL   ] SKIP unknown device in notice: {notice.device_id}")
            failures += 1
            continue

        local_dir = dest_root / notice.device_id
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / notice.filename
        remote = _remote_spec(args.ssh_user, target.ip, notice.remote_path)
        cmd = ["scp", "-q", remote, str(local_path)]

        print(f"[PULL   ] {notice.device_id} {notice.kind} -> {local_path}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=args.scp_timeout)
        if result.returncode != 0:
            failures += 1
            err = (result.stderr or result.stdout or "scp failed").strip()
            print(f"[PULL   ] ERROR {notice.device_id} {notice.remote_path}: {err}")

    if failures:
        print(f"\n[DONE   ] completed with {failures} pull failure(s)")
        return 5

    print(f"\n[DONE   ] pulled {len(unique_notices)} file(s) into {dest_root}")
    return 0 if all_ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
