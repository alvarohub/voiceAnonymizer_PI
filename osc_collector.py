#!/usr/bin/env python3
"""
osc_collector.py — Mac-side multi-device OSC capture & logger.

Listens on a single UDP port for OSC messages from many Raspberry Pis
(many microphones × many Pis). All messages are expected under the
   /dev/<device_id>/...
namespace emitted by strip_monitor.py (v2 protocol). Each unique
device_id gets its own CSV file, opened on first packet seen.

Architecture
────────────
  Pi A (mic 1) ─┐
  Pi A (mic 2) ─┤
    Pi B (mic 1) ─┼──► UDP 9000 ──► osc_collector.py ──► log_data/multi/<ts>_<dev>.csv
  Pi B (mic 2) ─┤
  …             ─┘

CSV schema (long format — one row per OSC message)
──────────────────────────────────────────────────
  recv_ts_iso, recv_ts_unix, sender_ip, device_id, address, args_json

We deliberately use a "long" schema (not per-feature columns) so the
file accepts any future OSC topic without schema migrations. Offline
analysis can pivot to wide format trivially.

Live console
────────────
Every 2 s, prints a compact table with per-device row rate, last RSS /
CPU% / temperature (from /stats/self), and age since last packet.

Usage
─────
  python osc_collector.py
    python osc_collector.py --port 9000 --out log_data/multi --idle-close 30

Stop with Ctrl-C — all open files are flushed and closed cleanly.

Dependencies: python-osc (pip install python-osc). PyYAML/numpy NOT used.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
except ImportError:
    print("ERROR: python-osc not installed. Run: pip install python-osc",
          file=sys.stderr)
    sys.exit(1)


# ───────────────────────── CLI ─────────────────────────
def parse_args():
    p = argparse.ArgumentParser(
        description="Multi-device OSC collector (one CSV per device).")
    p.add_argument("--port", type=int, default=9000,
                   help="UDP port to listen on (default 9000)")
    p.add_argument("--bind", default="0.0.0.0",
                   help="Interface to bind (default 0.0.0.0 = all)")
    p.add_argument("--out", default="log_data/multi",
                   help="Output directory (default log_data/multi)")
    p.add_argument("--idle-close", type=float, default=30.0,
                   help="Close a device's CSV after this many seconds of "
                        "silence; a new file opens if it comes back. "
                        "Default 30. Set 0 to disable auto-close.")
    p.add_argument("--flush-every", type=int, default=50,
                   help="Flush each CSV every N rows (default 50)")
    p.add_argument("--status-interval", type=float, default=2.0,
                   help="Seconds between live status table prints (default 2)")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress per-message debug printing")
    return p.parse_args()


# ───────────────────────── device registry ─────────────────────────
class DeviceRecord:
    """One CSV file + bookkeeping per device_id."""
    __slots__ = ("device_id", "sender_ip", "csv_path", "csv_file",
                 "csv_writer", "row_count", "first_seen", "last_seen",
                 "msg_times", "last_self_stats", "lock")

    def __init__(self, device_id: str, sender_ip: str, csv_path: Path):
        self.device_id = device_id
        self.sender_ip = sender_ip
        self.csv_path = csv_path
        self.csv_file = open(csv_path, "w", newline="", buffering=1)
        self.csv_writer = csv.writer(self.csv_file)
        self.csv_writer.writerow([
            "recv_ts_iso", "recv_ts_unix", "sender_ip",
            "device_id", "address", "args_json",
        ])
        now = time.time()
        self.row_count = 0
        self.first_seen = now
        self.last_seen = now
        # Recent message timestamps for live rate computation.
        self.msg_times: deque[float] = deque(maxlen=500)
        # Latest /stats/self payload: [rss_mb, cpu_pct, temp_c, n_threads]
        self.last_self_stats: list | None = None
        self.lock = threading.Lock()

    def write(self, recv_ts: float, sender_ip: str, address: str, args: list):
        iso = datetime.fromtimestamp(recv_ts, tz=timezone.utc).isoformat()
        try:
            args_json = json.dumps(args, default=str)
        except Exception:
            args_json = json.dumps([str(a) for a in args])
        with self.lock:
            self.csv_writer.writerow([
                iso, f"{recv_ts:.6f}", sender_ip,
                self.device_id, address, args_json,
            ])
            self.row_count += 1
            self.last_seen = recv_ts
            self.msg_times.append(recv_ts)

    def maybe_flush(self, every: int):
        with self.lock:
            if every > 0 and self.row_count % every == 0:
                try:
                    self.csv_file.flush()
                    os.fsync(self.csv_file.fileno())
                except Exception:
                    pass

    def close(self):
        with self.lock:
            try:
                self.csv_file.flush()
                self.csv_file.close()
            except Exception:
                pass

    def rate_hz(self, window_s: float = 2.0) -> float:
        cutoff = time.time() - window_s
        with self.lock:
            n = sum(1 for t in self.msg_times if t >= cutoff)
        return n / window_s


# ───────────────────────── Collector ─────────────────────────
class Collector:
    def __init__(self, args):
        self.args = args
        self.out_dir = Path(args.out)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.devices: dict[str, DeviceRecord] = {}
        self.devices_lock = threading.Lock()
        self.stop_event = threading.Event()
        # Pi registry from /hello packets (independent of CSV creation,
        # because a Pi may broadcast /hello before any /dev/<id>/... data).
        # device_id -> {ip, pi_id, mic_id, hostname, ctrl_port, version, last_hello}
        self.pi_registry: dict[str, dict] = {}

    # ─── /hello handler ───
    def on_hello(self, client_addr, address, *args):
        """v2: /hello <device_id> <pi_id> <mic_id> <hostname> <ctrl_port> <version>
        v1: /hello <pi_id> <hostname> <version>                     — legacy."""
        sender_ip = client_addr[0] if client_addr else "?"
        now = time.time()
        device_id = None
        info = {"ip": sender_ip, "last_hello": now}
        if len(args) >= 6:
            # v2
            device_id, pi_id, mic_id, hostname, ctrl_port, version = args[:6]
            info.update(pi_id=str(pi_id), mic_id=str(mic_id),
                        hostname=str(hostname), ctrl_port=int(ctrl_port),
                        version=str(version))
        elif len(args) >= 3:
            # v1 fallback — synthesise device_id from pi_id, default mic 1
            pi_id, hostname, version = args[:3]
            device_id = f"{pi_id}-1"
            info.update(pi_id=str(pi_id), mic_id="1",
                        hostname=str(hostname), ctrl_port=9001,
                        version=str(version) + "(legacy)")
        else:
            return
        device_id = str(device_id)
        prev = self.pi_registry.get(device_id)
        self.pi_registry[device_id] = info
        if prev is None:
            print(f"[HELLO] new device  {device_id}  from {sender_ip}  "
                  f"(host={info.get('hostname')}, ctrl={info.get('ctrl_port')}, "
                  f"v{info.get('version')})")

    # ─── data message handler (catches everything under /dev/*) ───
    def on_data(self, client_addr, address: str, *args):
        sender_ip = client_addr[0] if client_addr else "?"
        recv_ts = time.time()
        # Address must look like /dev/<device_id>/...
        parts = address.split("/", 3)
        # ['', 'dev', '<id>', 'rest...']
        if len(parts) < 3 or parts[1] != "dev":
            return
        device_id = parts[2]
        if not device_id:
            return
        rec = self._get_or_open(device_id, sender_ip)
        # Track latest /stats/self for the live table.
        if address == f"/dev/{device_id}/stats/self" and len(args) >= 1:
            rec.last_self_stats = list(args)
        rec.write(recv_ts, sender_ip, address, list(args))
        rec.maybe_flush(self.args.flush_every)
        if not self.args.quiet and rec.row_count <= 5:
            # First few packets per device: confirm they're arriving.
            print(f"  [{device_id}] {address} {args}")

    def _get_or_open(self, device_id: str, sender_ip: str) -> DeviceRecord:
        with self.devices_lock:
            rec = self.devices.get(device_id)
            if rec is not None:
                return rec
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = self.out_dir / f"{ts}_{device_id}.csv"
            rec = DeviceRecord(device_id, sender_ip, csv_path)
            self.devices[device_id] = rec
            print(f"[OPEN ] {device_id:>10s}  →  {csv_path}")
            return rec

    # ─── idle close ───
    def _idle_close_loop(self):
        if self.args.idle_close <= 0:
            return
        while not self.stop_event.wait(5.0):
            now = time.time()
            stale = []
            with self.devices_lock:
                for did, rec in list(self.devices.items()):
                    if now - rec.last_seen > self.args.idle_close:
                        stale.append(did)
                for did in stale:
                    rec = self.devices.pop(did)
                    rec.close()
                    print(f"[CLOSE] {did}  idle>{self.args.idle_close:g}s  "
                          f"({rec.row_count} rows)")

    # ─── live status table ───
    def _status_loop(self):
        interval = self.args.status_interval
        if interval <= 0:
            return
        while not self.stop_event.wait(interval):
            self._print_status()

    def _print_status(self):
        now = time.time()
        with self.devices_lock:
            items = list(self.devices.items())
        if not items:
            return
        lines = [
            "",
            f"╭─ Devices ── {datetime.now().strftime('%H:%M:%S')} "
            f"────────────────────────────────────────────╮",
            f"│ {'device_id':>10s}  {'ip':>15s}  {'rate':>7s}  "
            f"{'rows':>8s}  {'age':>5s}  {'RSS':>6s}  {'CPU':>5s}  {'°C':>5s} │",
        ]
        for did, rec in sorted(items):
            rate = rec.rate_hz()
            age = now - rec.last_seen
            rss = cpu = temp = "—"
            if rec.last_self_stats and len(rec.last_self_stats) >= 3:
                try:
                    rss = f"{float(rec.last_self_stats[0]):.0f}M"
                    cpu = f"{float(rec.last_self_stats[1]):.0f}%"
                    t = float(rec.last_self_stats[2])
                    temp = f"{t:.1f}" if t >= 0 else "—"
                except Exception:
                    pass
            lines.append(
                f"│ {did:>10s}  {rec.sender_ip:>15s}  {rate:>6.1f}/s  "
                f"{rec.row_count:>8d}  {age:>4.1f}s  "
                f"{rss:>6s}  {cpu:>5s}  {temp:>5s} │")
        lines.append("╰" + "─" * (len(lines[1]) - 2) + "╯")
        print("\n".join(lines))

    # ─── lifecycle ───
    def run(self):
        disp = Dispatcher()
        disp.map("/hello", self.on_hello, needs_reply_address=True)
        # Catch-all for any /dev/<id>/... topic. python-osc supports glob
        # patterns; we use a permissive '*' and filter inside on_data.
        disp.set_default_handler(self.on_data, needs_reply_address=True)

        try:
            server = ThreadingOSCUDPServer(
                (self.args.bind, self.args.port), disp)
        except OSError as e:
            print(f"ERROR: bind {self.args.bind}:{self.args.port} failed: {e}",
                  file=sys.stderr)
            sys.exit(1)

        host_ip = _best_local_ip()
        print(f"[BIND ] {self.args.bind}:{self.args.port}  "
              f"(reachable as {host_ip}:{self.args.port})")
        print(f"[OUT  ] {self.out_dir.resolve()}")
        print(f"[CFG  ] idle_close={self.args.idle_close}s  "
              f"flush_every={self.args.flush_every}  "
              f"status_interval={self.args.status_interval}s")
        print("Waiting for /hello broadcasts and /dev/<id>/... messages…")
        print("Ctrl-C to stop.\n")

        threading.Thread(target=self._idle_close_loop,
                         daemon=True, name="idle_close").start()
        threading.Thread(target=self._status_loop,
                         daemon=True, name="status").start()

        def _shutdown(*_):
            print("\n[STOP ] shutting down…")
            self.stop_event.set()
            try:
                server.shutdown()
            except Exception:
                pass
        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        try:
            server.serve_forever()
        finally:
            self.close_all()

    def close_all(self):
        with self.devices_lock:
            for did, rec in self.devices.items():
                rec.close()
                print(f"[CLOSE] {did}  ({rec.row_count} rows)  {rec.csv_path}")
            self.devices.clear()


def _best_local_ip() -> str:
    """Best-effort outbound interface IP (no packet actually sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    args = parse_args()
    Collector(args).run()
