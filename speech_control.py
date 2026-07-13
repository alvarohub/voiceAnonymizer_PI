#!/usr/bin/env python3
"""Operator control CLI and Python API for speech logging processes.

This is the script-oriented companion to the browser receiver. It reads the
central bridge registry, sends /ctrl OSC commands to one process or many, and
waits for the same application-level ACKs used by the GUI.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import urlopen

ACK_SENTINEL = "__ack__"
DEFAULT_BRIDGE = "http://localhost:3000"
DEFAULT_ACK_TIMEOUT = 0.5
DEFAULT_SESSION_FILE = "start_recording_session.yaml"
DEFAULT_HELLO_PORT = 9000
DEFAULT_DISCOVERY_SECONDS = 5.0
DEFAULT_LOG_MAX_MINUTES = 60.0
KNOWN_COMMANDS = {
    "query_state",
    "osc_start",
    "osc_stop",
    "osc_send_hz",
    "log_start",
    "log_pause",
    "log_resume",
    "log_save_stop",
    "log_discard_stop",
    "audio_reconnect",
    "vad_on",
    "vad_off",
    "emotion_on",
    "emotion_off",
    "prosody_on",
    "prosody_off",
}


@dataclass(frozen=True)
class Target:
    device_id: str
    pi_id: str
    mic_id: str
    hostname: str
    ip: str
    ctrl_port: int
    audio_ok: bool | None = None
    audio_error: str = ""
    age_ms: int = 0

    @property
    def label(self) -> str:
        mic = f" / M{self.mic_id}" if self.mic_id and self.mic_id != "?" else ""
        name = self.pi_id or self.hostname or self.device_id or "process"
        return f"{name}{mic} {self.ip}".strip()

    @property
    def endpoint(self) -> str:
        return f"{self.ip}:{self.ctrl_port}"


@dataclass(frozen=True)
class Ack:
    target: Target
    command: str
    ok: bool
    message: str
    elapsed_ms: int | None
    timed_out: bool = False


@dataclass(frozen=True)
class SessionPlan:
    path: Path
    bridge: str
    ack_timeout: float
    targets: list[Target]
    commands: list[tuple[str, list[str]]]


def _need_python_osc(exc: ImportError) -> SystemExit:
    print("ERROR: python-osc is required. Install it with: pip install python-osc", file=sys.stderr)
    return SystemExit(1)


def _need_yaml(exc: ImportError) -> SystemExit:
    print("ERROR: PyYAML is required. Install it with: pip install PyYAML", file=sys.stderr)
    return SystemExit(1)


def osc_line(command: str, args: Iterable[str] = ()) -> str:
    command = command[6:] if command.startswith("/ctrl/") else command
    values = [str(a) for a in args]
    return f"/ctrl/{command}" + (" " + " ".join(json.dumps(v) for v in values) if values else "")


def parse_target_endpoint(value: str) -> Target:
    text = value.removeprefix("udp://")
    if ":" not in text:
        raise ValueError("target must look like 192.168.1.49:9001 or udp://192.168.1.49:9001")
    host, port_text = text.rsplit(":", 1)
    return Target(
        device_id=f"direct:{host}:{port_text}",
        pi_id="direct",
        mic_id="",
        hostname="direct",
        ip=host,
        ctrl_port=int(port_text),
    )


def _sort_part(value: str):
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _target_sort_key(target: Target):
    return (_sort_part(target.pi_id), _sort_part(target.mic_id), target.device_id)


def _parse_hello(sender_ip: str, args: tuple[Any, ...]) -> Target | None:
    if len(args) >= 6:
        device_id, pi_id, mic_id, hostname, ctrl_port, _version = args[:6]
        return Target(
            device_id=str(device_id),
            pi_id=str(pi_id),
            mic_id=str(mic_id),
            hostname=str(hostname),
            ip=sender_ip,
            ctrl_port=int(ctrl_port),
        )

    if len(args) >= 3:
        pi_id, hostname, _version = args[:3]
        return Target(
            device_id=f"{pi_id}-1",
            pi_id=str(pi_id),
            mic_id="1",
            hostname=str(hostname),
            ip=sender_ip,
            ctrl_port=9001,
        )

    return None


def discover_hello_targets(port: int = DEFAULT_HELLO_PORT,
                           listen_seconds: float = DEFAULT_DISCOVERY_SECONDS) -> list[Target]:
    """Listen for /hello heartbeats and return the processes currently present."""
    try:
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import ThreadingOSCUDPServer
    except ImportError as exc:
        raise _need_python_osc(exc) from exc

    targets: dict[str, Target] = {}
    lock = threading.Lock()

    def on_hello(client_addr, address, *args):
        sender_ip = client_addr[0] if client_addr else "?"
        target = _parse_hello(sender_ip, args)
        if target is None:
            return
        with lock:
            targets[target.device_id] = target

    dispatcher = Dispatcher()
    dispatcher.map("/hello", on_hello, needs_reply_address=True)

    try:
        server = ThreadingOSCUDPServer(("0.0.0.0", port), dispatcher)
    except OSError as exc:
        raise RuntimeError(
            f"cannot listen for /hello on UDP :{port}; another process may already be using that port"
        ) from exc

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="hello-discovery")
    thread.start()
    print(f"LISTENING for /hello heartbeats on UDP :{port} for {listen_seconds:g}s")
    try:
        time.sleep(listen_seconds)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)

    with lock:
        discovered = list(targets.values())
    return sorted(discovered, key=_target_sort_key)


def load_bridge_targets(bridge: str = DEFAULT_BRIDGE, max_age_ms: int = 8000) -> list[Target]:
    """Read the process list from receiver/bridge.js.

    This is the preferred discovery method while the browser receiver is open.
    It returns the same processes shown in the GUI's Pis And Microphones panel.
    """
    url = bridge.rstrip("/") + "/api/devices"
    try:
        with urlopen(url, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError(f"cannot read bridge device registry at {url}: {exc}") from exc

    targets: list[Target] = []
    for item in payload.get("devices", []):
        age_ms = int(item.get("ageMs") or 0)
        if max_age_ms and age_ms > max_age_ms:
            continue
        targets.append(
            Target(
                device_id=str(item.get("device_id") or ""),
                pi_id=str(item.get("pi_id") or item.get("hostname") or ""),
                mic_id=str(item.get("mic_id") or ""),
                hostname=str(item.get("hostname") or ""),
                ip=str(item.get("addr") or ""),
                ctrl_port=int(item.get("ctrl_port") or 9001),
                audio_ok=item.get("audio_ok") if item.get("audio_ok") is None else bool(item.get("audio_ok")),
                audio_error=str(item.get("audio_error") or ""),
                age_ms=age_ms,
            )
        )
    return sorted(targets, key=lambda t: (t.pi_id, t.mic_id, t.device_id))


def _ack_server(expected_id: str, result: dict, ready: threading.Event, done: threading.Event):
    try:
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import ThreadingOSCUDPServer
    except ImportError as exc:
        raise _need_python_osc(exc) from exc

    def on_ack(address, *args):
        # /dev/<device_id>/ack <command> <cmd_id> <ok> <message>
        if len(args) < 3:
            return
        cmd_id = str(args[1])
        if cmd_id != expected_id:
            return
        ok_raw = args[2]
        ok = ok_raw is True or ok_raw == 1 or ok_raw == "1" or str(ok_raw).lower() == "true"
        result["command"] = str(args[0])
        result["ok"] = ok
        result["message"] = str(args[3]) if len(args) >= 4 else ("ok" if ok else "error")
        result["received_at"] = time.time()
        done.set()

    dispatcher = Dispatcher()
    dispatcher.map("/dev/*/ack", on_ack)
    server = ThreadingOSCUDPServer(("0.0.0.0", 0), dispatcher)
    result["ack_port"] = int(server.server_address[1])
    ready.set()
    try:
        while not done.is_set():
            server.handle_request()
    finally:
        server.server_close()


def _log_start_args(args: list[str]) -> list[str]:
    if args:
        if len(args) == 1:
            # Convenience form: log_start <max_minutes>
            try:
                numeric = float(args[0])
            except ValueError:
                return args
            if 0 < numeric < 10000:
                now_ms = int(time.time() * 1000)
                iso = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")
                return [str(now_ms), iso, str(now_ms), iso, f"{numeric:g}"]
            return args
        if len(args) == 2:
            # Backward compatibility: session_start pair only.
            return [str(args[0]), str(args[1]), str(args[0]), str(args[1]), f"{DEFAULT_LOG_MAX_MINUTES:g}"]
        if len(args) == 4:
            # Backward compatibility: no max_minutes provided.
            return [*args, f"{DEFAULT_LOG_MAX_MINUTES:g}"]
        return args
    now_ms = int(time.time() * 1000)
    iso = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")
    return [str(now_ms), iso, str(now_ms), iso, f"{DEFAULT_LOG_MAX_MINUTES:g}"]


def _prepare_args(command: str, args: Iterable[str]) -> list[str]:
    values = [str(a) for a in args]
    return _log_start_args(values) if command == "log_start" else values


def _device_scoped_save_args(command: str, args: list[str], target: Target) -> list[str]:
    if command != "log_save_stop" or not args:
        return args
    name = args[0]
    if not name:
        return args
    safe_id = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in target.device_id or target.mic_id)
    stem = name[:-4] if name.lower().endswith(".csv") else name
    return [f"{stem}_{safe_id}.csv", *args[1:]]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"session YAML not found: {path}")
    try:
        import yaml
    except ImportError:
        return _load_simple_session_yaml(path)

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"session YAML must contain a mapping at the top level: {path}")
    return data


def _strip_yaml_comment(line: str) -> str:
    quote: str | None = None
    for index, char in enumerate(line):
        if char in {'"', "'"}:
            quote = None if quote == char else char if quote is None else quote
        elif char == "#" and quote is None:
            return line[:index].rstrip()
    return line.rstrip()


def _parse_simple_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return {}
    if text in {"null", "None", "~"}:
        return None
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [] if not inner else [_parse_simple_scalar(part) for part in inner.split(",")]
    if (text.startswith("'") and text.endswith("'")) or (text.startswith('"') and text.endswith('"')):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _load_simple_session_yaml(path: Path) -> dict[str, Any]:
    """Small fallback parser for start_recording_session.yaml when PyYAML is absent."""
    data: dict[str, Any] = {}
    section: str | None = None
    current_item: dict[str, Any] | None = None
    list_sections = {"pis", "processes"}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        clean = _strip_yaml_comment(raw_line)
        if not clean.strip():
            continue
        indent = len(clean) - len(clean.lstrip(" "))
        line = clean.strip()

        if indent == 0:
            current_item = None
            if line.endswith(":"):
                section = line[:-1].strip()
                data[section] = [] if section in list_sections else {}
                continue
            if ":" not in line:
                raise ValueError(f"cannot parse YAML line: {raw_line}")
            key, value = line.split(":", 1)
            section = None
            data[key.strip()] = _parse_simple_scalar(value)
            continue

        if section is None:
            raise ValueError(f"nested YAML value has no section: {raw_line}")

        if section in list_sections:
            if indent == 2 and line.startswith("- "):
                current_item = {}
                data[section].append(current_item)
                rest = line[2:].strip()
                if rest:
                    if ":" not in rest:
                        raise ValueError(f"cannot parse YAML list item: {raw_line}")
                    key, value = rest.split(":", 1)
                    current_item[key.strip()] = _parse_simple_scalar(value)
                continue
            if current_item is None or ":" not in line:
                raise ValueError(f"cannot parse YAML list field: {raw_line}")
            key, value = line.split(":", 1)
            current_item[key.strip()] = _parse_simple_scalar(value)
            continue

        if ":" not in line:
            raise ValueError(f"cannot parse YAML field: {raw_line}")
        key, value = line.split(":", 1)
        section_value = data.setdefault(section, {})
        if not isinstance(section_value, dict):
            raise ValueError(f"YAML section {section} cannot contain fields")
        section_value[key.strip()] = _parse_simple_scalar(value)

    return data


def _bool_value(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{field} must be true or false")


def _as_mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    return value


def _as_list(value: Any, field: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise ValueError(f"{field} must be a list")


def _address(value: dict[str, Any], field: str) -> str:
    ip = value.get("ip") or value.get("host") or value.get("addr")
    if not ip:
        raise ValueError(f"{field} needs an ip field")
    return str(ip)


def _default_ctrl_port(mic_id: str) -> int:
    try:
        return 9000 + int(mic_id)
    except ValueError as exc:
        raise ValueError(f"mic_id must be numeric when ctrl_port is omitted: {mic_id!r}") from exc


def _port_from_map(ctrl_ports: Any, mic_id: str) -> int | None:
    if ctrl_ports is None:
        return None
    if not isinstance(ctrl_ports, dict):
        raise ValueError("ctrl_ports must be a mapping such as {1: 9001, 2: 9002}")
    for key in (mic_id, str(mic_id)):
        if key in ctrl_ports:
            return int(ctrl_ports[key])
    try:
        numeric_key = int(mic_id)
    except ValueError:
        return None
    return int(ctrl_ports[numeric_key]) if numeric_key in ctrl_ports else None


def _session_target(pi: dict[str, Any], mic_entry: Any) -> Target:
    ip = _address(pi, "pi entry")
    if isinstance(mic_entry, dict):
        mic_id = str(mic_entry.get("mic_id") or mic_entry.get("id") or mic_entry.get("mic"))
        if not mic_id or mic_id == "None":
            raise ValueError("mic entry needs mic_id, id, or mic")
        ctrl_port = int(mic_entry.get("ctrl_port") or _port_from_map(pi.get("ctrl_ports"), mic_id) or _default_ctrl_port(mic_id))
        device_id = str(mic_entry.get("device_id") or f"{pi.get('pi_id') or pi.get('hostname') or ip}-{mic_id}")
    else:
        mic_id = str(mic_entry)
        ctrl_port = int(_port_from_map(pi.get("ctrl_ports"), mic_id) or _default_ctrl_port(mic_id))
        device_id = f"{pi.get('pi_id') or pi.get('hostname') or ip}-{mic_id}"
    pi_id = str(pi.get("pi_id") or pi.get("id") or pi.get("hostname") or ip)
    return Target(
        device_id=device_id,
        pi_id=pi_id,
        mic_id=mic_id,
        hostname=str(pi.get("hostname") or pi_id),
        ip=ip,
        ctrl_port=ctrl_port,
    )


def _load_session_targets(data: dict[str, Any]) -> list[Target]:
    targets: list[Target] = []
    if data.get("processes") is not None:
        for index, process in enumerate(_as_list(data.get("processes"), "processes"), start=1):
            if not isinstance(process, dict):
                raise ValueError(f"processes[{index}] must be a mapping")
            mic_id = str(process.get("mic_id") or process.get("mic") or "")
            if not mic_id:
                raise ValueError(f"processes[{index}] needs mic_id")
            ctrl_port = int(process.get("ctrl_port") or _default_ctrl_port(mic_id))
            pi_id = str(process.get("pi_id") or process.get("hostname") or process.get("ip") or f"process-{index}")
            targets.append(
                Target(
                    device_id=str(process.get("device_id") or f"{pi_id}-{mic_id}"),
                    pi_id=pi_id,
                    mic_id=mic_id,
                    hostname=str(process.get("hostname") or pi_id),
                    ip=_address(process, f"processes[{index}]"),
                    ctrl_port=ctrl_port,
                )
            )
    else:
        for index, pi in enumerate(_as_list(data.get("pis"), "pis"), start=1):
            if not isinstance(pi, dict):
                raise ValueError(f"pis[{index}] must be a mapping")
            mics = pi.get("mics", [1, 2])
            for mic_entry in _as_list(mics, f"pis[{index}].mics"):
                targets.append(_session_target(pi, mic_entry))

    if not targets:
        raise ValueError("session YAML must list expected processes under pis or processes")
    seen: set[str] = set()
    for target in targets:
        key = f"{target.ip}:{target.ctrl_port}"
        if key in seen:
            raise ValueError(f"duplicate target endpoint in session YAML: {key}")
        seen.add(key)
    return sorted(targets, key=lambda t: (t.pi_id, t.mic_id, t.ctrl_port))


def _session_commands(data: dict[str, Any]) -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    processing = _as_mapping(data.get("processing") or data.get("record"), "processing")
    for stage in ("vad", "prosody", "emotion"):
        if stage in processing:
            suffix = "on" if _bool_value(processing[stage], f"processing.{stage}") else "off"
            commands.append((f"{stage}_{suffix}", []))

    telemetry = data.get("osc")
    if telemetry is not None:
        if isinstance(telemetry, dict):
            if "active" in telemetry:
                commands.append(("osc_start" if _bool_value(telemetry["active"], "osc.active") else "osc_stop", []))
            if "send_hz" in telemetry:
                commands.append(("osc_send_hz", [str(telemetry["send_hz"])]))
        else:
            commands.append(("osc_start" if _bool_value(telemetry, "osc") else "osc_stop", []))

    logging = _as_mapping(data.get("logging"), "logging")
    should_start = _bool_value(logging.get("start", True), "logging.start")
    if should_start:
        max_minutes = float(logging.get("max_minutes", DEFAULT_LOG_MAX_MINUTES))
        if max_minutes <= 0:
            raise ValueError("logging.max_minutes must be > 0")
        commands.append(("log_start", [f"{max_minutes:g}"]))
    return commands


def load_session_plan(path: str | Path = DEFAULT_SESSION_FILE) -> SessionPlan:
    session_path = Path(path)
    data = _load_yaml(session_path)
    return SessionPlan(
        path=session_path,
        bridge=str(data.get("bridge") or DEFAULT_BRIDGE),
        ack_timeout=float(data.get("ack_timeout") or DEFAULT_ACK_TIMEOUT),
        targets=_load_session_targets(data),
        commands=_session_commands(data),
    )


def ack_ready_for_recording(result: Ack) -> bool:
    if not result.ok or result.timed_out:
        return False
    message = result.message.lower()
    if "audio=failure" in message or result.target.audio_ok is False:
        return False
    return "audio=ok" in message or result.target.audio_ok is True


def send_ctrl(target: Target, command: str, args: Iterable[str] = (), ack_timeout: float = DEFAULT_ACK_TIMEOUT,
              dry_run: bool = False) -> Ack:
    """Send one OSC /ctrl command to one process and wait for ACK."""
    command = command[6:] if command.startswith("/ctrl/") else command
    if command not in KNOWN_COMMANDS:
        raise ValueError(f"unknown control command: {command}")
    values = _prepare_args(command, args)
    send_line = f"OSC SEND udp://{target.endpoint} {osc_line(command, values)}"
    if dry_run:
        print(f"--> {send_line}    # {target.label}")
        return Ack(target, command, True, "dry-run", 0)

    try:
        from pythonosc.udp_client import SimpleUDPClient
    except ImportError as exc:
        raise _need_python_osc(exc) from exc

    cmd_id = f"ctrl-{uuid.uuid4().hex[:12]}"

    result: dict = {}
    ready = threading.Event()
    done = threading.Event()
    thread = threading.Thread(target=_ack_server, args=(cmd_id, result, ready, done), daemon=True)
    thread.start()
    ready.wait(timeout=1.0)
    ack_port = int(result["ack_port"])

    client = SimpleUDPClient(target.ip, target.ctrl_port)
    send_args = [*values, ACK_SENTINEL, cmd_id, str(ack_port)]
    sent_at = time.time()
    print(f"--> {send_line}    # {target.label}")
    client.send_message(f"/ctrl/{command}", send_args)

    done.wait(timeout=ack_timeout)
    done.set()
    try:
        # Unblock handle_request if needed.
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"", ("127.0.0.1", ack_port))
    except OSError:
        pass
    thread.join(timeout=0.2)

    if "received_at" not in result:
        print(f"<-- {target.label}  TIMEOUT after {int(ack_timeout * 1000)}ms")
        return Ack(target, command, False, "no ACK received", None, timed_out=True)

    elapsed_ms = int(round((float(result["received_at"]) - sent_at) * 1000))
    ok = bool(result.get("ok"))
    message = str(result.get("message") or ("ok" if ok else "error"))
    status = "OK" if ok else "ERROR"
    print(f"<-- {target.label}  {status} {elapsed_ms}ms: {message}")
    return Ack(target, command, ok, message, elapsed_ms)


def broadcast_ctrl(targets: Iterable[Target], command: str, args: Iterable[str] = (),
                   ack_timeout: float = DEFAULT_ACK_TIMEOUT, dry_run: bool = False,
                   only_audio_ok: bool = True) -> list[Ack]:
    """Fan out one command to many processes.

    By default this matches GUI BROADCAST: one OSC packet is sent independently
    to each online process with audio_ok true. This is not UDP subnet broadcast.
    """
    selected = [t for t in targets if (not only_audio_ok or t.audio_ok is True)]
    if not selected:
        print("ERROR: no broadcast targets (online audio-ok processes) found", file=sys.stderr)
        return []
    command_name = command[6:] if command.startswith("/ctrl/") else command
    base_args = _prepare_args(command_name, args)
    results: list[Ack | None] = [None] * len(selected)
    print(f"OSC FANOUT {osc_line(command_name, base_args)}  TO {len(selected)} process(es)")
    threads = []

    def _worker(index: int, target: Target):
        per_target_args = _device_scoped_save_args(command_name, list(base_args), target)
        results[index] = send_ctrl(target, command_name, per_target_args, ack_timeout=ack_timeout, dry_run=dry_run)

    for idx, target in enumerate(selected):
        thread = threading.Thread(target=_worker, args=(idx, target), daemon=True)
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()

    return [r for r in results if r is not None]


def find_target(targets: Iterable[Target], device_id: str | None) -> Target:
    if not device_id:
        raise ValueError("--device is required unless --to is used")
    for target in targets:
        if target.device_id == device_id:
            return target
    raise ValueError(f"device {device_id!r} was not found in the bridge registry")


def health_check(bridge: str = DEFAULT_BRIDGE, ack_timeout: float = DEFAULT_ACK_TIMEOUT) -> list[Ack]:
    """Query every discovered process and print OK/ERROR, including mic failures."""
    targets = load_bridge_targets(bridge)
    if not targets:
        print("ERROR: no active processes in bridge registry")
        return []

    print("process                     endpoint              health")
    print("--------------------------  --------------------  ------")
    results = []
    for target in targets:
        result = send_ctrl(target, "query_state", ack_timeout=ack_timeout)
        ok = ack_ready_for_recording(result)
        status = "OK" if ok else "ERROR"
        extra = target.audio_error if target.audio_ok is False and target.audio_error else result.message
        print(f"{target.label:<26s}  {target.endpoint:<20s}  {status} {extra}")
        results.append(result)
    return results


def test_targets(targets: Iterable[Target], ack_timeout: float = DEFAULT_ACK_TIMEOUT,
                 dry_run: bool = False) -> list[Ack]:
    """Query an explicit target list and require every listed mic process to be ready."""
    selected = list(targets)
    if not selected:
        print("ERROR: no expected processes listed")
        return []

    print(f"expected processes: {len(selected)}")
    print("process                     endpoint              health")
    print("--------------------------  --------------------  ------")
    results = []
    for target in selected:
        result = send_ctrl(target, "query_state", ack_timeout=ack_timeout, dry_run=dry_run)
        ok = True if dry_run else ack_ready_for_recording(result)
        status = "OK" if ok else "ERROR"
        print(f"{target.label:<26s}  {target.endpoint:<20s}  {status} {result.message}")
        results.append(result)
    return results


def test_session_file(path: str | Path = DEFAULT_SESSION_FILE, ack_timeout: float | None = None,
                      dry_run: bool = False) -> list[Ack]:
    plan = load_session_plan(path)
    timeout = plan.ack_timeout if ack_timeout is None else ack_timeout
    print(f"SESSION TEST {plan.path}")
    return test_targets(plan.targets, ack_timeout=timeout, dry_run=dry_run)


def start_recording_session(path: str | Path = DEFAULT_SESSION_FILE, ack_timeout: float | None = None,
                            dry_run: bool = False) -> list[Ack]:
    """Preflight an exact rig from YAML, then fan out configured start commands."""
    plan = load_session_plan(path)
    timeout = plan.ack_timeout if ack_timeout is None else ack_timeout
    print(f"START RECORDING SESSION {plan.path}")
    preflight = test_targets(plan.targets, ack_timeout=timeout, dry_run=dry_run)
    if not preflight or (not dry_run and not all(ack_ready_for_recording(result) for result in preflight)):
        print("ERROR: session was not started because one or more expected mic processes failed preflight", file=sys.stderr)
        return preflight

    results = list(preflight)
    for command, values in plan.commands:
        command_results = broadcast_ctrl(
            plan.targets,
            command,
            values,
            ack_timeout=timeout,
            dry_run=dry_run,
            only_audio_ok=False,
        )
        results.extend(command_results)
        if not command_results or (not dry_run and not all(result.ok for result in command_results)):
            print(f"ERROR: stopping session startup after failed {command}", file=sys.stderr)
            break
    return results


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Control speech logging processes from scripts.")
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE, help="receiver bridge URL (default http://localhost:3000)")
    parser.add_argument("--ack-timeout", type=float, default=DEFAULT_ACK_TIMEOUT, help="ACK timeout in seconds")
    sub = parser.add_subparsers(dest="mode", required=True)

    health = sub.add_parser("health", help="Alias for test; report OK/ERROR mic/control status")
    health.add_argument("--session", help="YAML file with the expected Pi/mic list to test exactly")
    health.add_argument("--hello-port", type=int, default=DEFAULT_HELLO_PORT,
                        help=f"UDP port to listen on for /hello heartbeats (default {DEFAULT_HELLO_PORT})")
    health.add_argument("--listen-seconds", type=float, default=DEFAULT_DISCOVERY_SECONDS,
                        help=f"seconds to listen for /hello when no session YAML is given (default {DEFAULT_DISCOVERY_SECONDS:g})")

    test = sub.add_parser("test", help="Test at any time if processes and microphones are OK")
    test.add_argument("session", nargs="?", help="YAML file with the expected Pi/mic list to test exactly")
    test.add_argument("--dry-run", action="store_true", help="print expected OSC sends without sending them")
    test.add_argument("--hello-port", type=int, default=DEFAULT_HELLO_PORT,
                      help=f"UDP port to listen on for /hello heartbeats (default {DEFAULT_HELLO_PORT})")
    test.add_argument("--listen-seconds", type=float, default=DEFAULT_DISCOVERY_SECONDS,
                      help=f"seconds to listen for /hello when no session YAML is given (default {DEFAULT_DISCOVERY_SECONDS:g})")

    send = sub.add_parser("send", help="Send one /ctrl command to one process")
    send.add_argument("--device", help="device_id from the GUI, e.g. local-1 or 5-2")
    send.add_argument("--to", help="direct OSC target, e.g. 192.168.1.49:9001")
    send.add_argument("--dry-run", action="store_true", help="print what would be sent")
    send.add_argument("command", help="control command, e.g. log_pause or /ctrl/log_pause")
    send.add_argument("args", nargs="*", help="OSC command arguments")

    broad = sub.add_parser("broadcast", help="Send one /ctrl command to all online audio-ok processes")
    broad.add_argument("--all", action="store_true", help="include audio-failure processes too")
    broad.add_argument("--dry-run", action="store_true", help="print what would be sent")
    broad.add_argument("--session", help="use the exact target list from a session YAML instead of bridge discovery")
    broad.add_argument("command", help="control command, e.g. log_start or /ctrl/log_start")
    broad.add_argument("args", nargs="*", help="OSC command arguments")

    session = sub.add_parser(
        "start-recording-session",
        aliases=["start_recording_session"],
        help="Preflight an expected rig YAML, then start the configured recording session",
    )
    session.add_argument("session", nargs="?", default=DEFAULT_SESSION_FILE,
                         help=f"session YAML file (default {DEFAULT_SESSION_FILE})")
    session.add_argument("--dry-run", action="store_true", help="print expected OSC sends without sending them")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.mode in {"health", "test"}:
            session_path = getattr(args, "session", None)
            if session_path:
                dry_run = getattr(args, "dry_run", False)
                results = test_session_file(session_path, ack_timeout=args.ack_timeout, dry_run=dry_run)
                if dry_run:
                    return 0 if results else 2
            else:
                if getattr(args, "dry_run", False):
                    print("ERROR: --dry-run needs a session YAML so there is a fixed target list", file=sys.stderr)
                    return 2
                try:
                    targets = discover_hello_targets(args.hello_port, args.listen_seconds)
                except RuntimeError as exc:
                    # Common case: bridge already owns UDP :9000. Fall back to its registry.
                    print(f"WARNING: {exc}", file=sys.stderr)
                    print(f"WARNING: falling back to bridge discovery at {args.bridge}", file=sys.stderr)
                    targets = load_bridge_targets(args.bridge)
                results = test_targets(targets, ack_timeout=args.ack_timeout)
            return 0 if results and all(ack_ready_for_recording(result) for result in results) else 2

        if args.mode == "send":
            if args.to:
                target = parse_target_endpoint(args.to)
            else:
                target = find_target(load_bridge_targets(args.bridge), args.device)
            result = send_ctrl(target, args.command, args.args, args.ack_timeout, dry_run=args.dry_run)
            return 0 if result.ok else 3

        if args.mode == "broadcast":
            targets = load_session_plan(args.session).targets if args.session else load_bridge_targets(args.bridge)
            results = broadcast_ctrl(
                targets,
                args.command,
                args.args,
                ack_timeout=args.ack_timeout,
                dry_run=args.dry_run,
                only_audio_ok=False if args.session else not args.all,
            )
            return 0 if results and all(r.ok for r in results) else 3

        if args.mode in {"start-recording-session", "start_recording_session"}:
            results = start_recording_session(args.session, ack_timeout=args.ack_timeout, dry_run=args.dry_run)
            preflight_count = len(load_session_plan(args.session).targets)
            preflight = results[:preflight_count]
            commands = results[preflight_count:]
            if not preflight or (not args.dry_run and not all(ack_ready_for_recording(result) for result in preflight)):
                return 2
            return 0 if args.dry_run or (commands and all(result.ok for result in commands)) else 3
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
