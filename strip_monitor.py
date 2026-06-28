#!/usr/bin/env python3
"""
Real-time Speech Analysis — Prosody LLD + VAD + Emotion

Architecture:
  - sounddevice callback → rolling audio buffer
  - openSMILE thread: short-window + overlap → 10ms prosody frames → _frame_buf
  - Emotion thread: sliding window + hop → emotion predictions → _emo_buf
  - Logger thread: configurable rate → CSV + OSC (reads both buffers)
  - Display (FuncAnimation): reads both ring buffers for rendering

All parameters live in config.yaml; CLI args override.

Usage:
    conda activate ML311
    python strip_monitor.py                       # with display
    python strip_monitor.py --no-display          # headless (log/OSC only)
    python strip_monitor.py --emotion-model seed  # smaller model
"""

from __future__ import annotations

import argparse
import collections
import csv
import itertools
import os
import sys
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import opensmile
import torch
import yaml
from scipy.signal import resample_poly


# ═══════════════════════════════════════════════════════════════════
# 0. ARGUMENT PARSING + CONFIG LOADING
# ═══════════════════════════════════════════════════════════════════
def parse_args():
    p = argparse.ArgumentParser(description="Real-time speech analysis")
    p.add_argument("--config", default="config.yaml",
                   help="Path to YAML config file")
    p.add_argument("--no-display", action="store_true",
                   help="Run headless (no matplotlib window)")
    p.add_argument("--no-emotion", action="store_true",
                   help="Start with emotion processing inactive (model still "
                        "loads so it can be toggled on at runtime). Overrides "
                        "emotion_active in config.")
    p.add_argument("--no-emotion-load", action="store_true",
                   help="Do not load the emotion model at startup. This saves "
                        "memory, but emotion processing cannot be activated at runtime.")
    p.add_argument("--no-vad", action="store_true",
                   help="Disable Silero VAD (overrides vad_active in config). "
                        "Useful for isolating prosody on slow hardware.")
    p.add_argument("--no-prosody", action="store_true",
                   help="Disable openSMILE prosody thread (overrides "
                        "prosody_active in config).")
    p.add_argument("--emotion-model", choices=["base", "seed"], default=None,
                   help="Emotion model size: base (~90M) or seed (~20M)")
    p.add_argument("--osc-ip", default=None)
    p.add_argument("--osc-port", type=int, default=None)
    p.add_argument("--osc-prefix", default=None)
    p.add_argument("--osc-autostart", action="store_true",
                   help="Start OSC streaming automatically on launch")
    p.add_argument("--ctrl-port", type=int, default=None,
                   help="Port to listen for remote control OSC commands. "
                        "Defaults to ctrl_port in config (or 9001).")
    p.add_argument("--pi-id", default=None,
                   help="Raspberry Pi identifier (e.g. '5'). Overrides "
                        "pi_id in config.")
    p.add_argument("--mic-id", default=None,
                   help="Microphone identifier within this Pi (e.g. '1' or "
                        "'2'). Overrides mic_id in config. Combined with "
                        "pi_id to form device_id like '5-2'.")
    p.add_argument("--device", default=None,
                   help="Input device: integer index OR substring of device name "
                        "(e.g. 'HK-MIC1'). Use --list-devices to see options. "
                        "Overrides 'audio_device' in config.yaml.")
    p.add_argument("--list-devices", action="store_true",
                   help="List available audio input devices and exit")
    p.add_argument("--no-resample", action="store_true",
                   help="Disable native-rate auto-detect and in-callback "
                        "resampling. Forces the stream to open at SR (16 kHz). "
                        "Use only if your mic natively supports SR (most USB "
                        "mics are 44.1/48 kHz and will fail to open on Linux "
                        "without resampling).")
    return p.parse_args()


def list_input_devices():
    """Print all input-capable audio devices."""
    import sounddevice as sd
    print("Available audio input devices:")
    default_in = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = " *" if i == default_in else "  "
            print(f"  [{i}]{mark} {d['name']}  "
                  f"(in={d['max_input_channels']}, sr={int(d['default_samplerate'])})")
    print("  (* = system default)")


def resolve_device(spec):
    """Resolve a device spec (int, digit-string, or name substring) to an index.
    Returns None on failure or if spec is None/empty."""
    if spec is None or spec == "":
        return None
    import sounddevice as sd
    # Integer index
    if isinstance(spec, int):
        return spec
    s = str(spec).strip()
    if s.isdigit():
        return int(s)
    # Name substring (case-insensitive)
    sl = s.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and sl in d["name"].lower():
            return i
    print(f"[WARN] No input device matching '{spec}' — using system default")
    return None


def load_config(args) -> dict:
    """Load config.yaml, then overlay CLI args where provided."""
    cfg_path = Path(args.config)
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        print(f"[CFG] loaded {cfg_path}")
    else:
        cfg = {}
        print(f"[CFG] {cfg_path} not found — using defaults")

    # Defaults
    defaults = dict(
        sample_rate=16000,
        opensmile_interval=1.0,
        opensmile_margin=0.2,
        emo_window=2.0,
        emo_hop=0.5,
        vad_threshold=0.3,
        vad_interval=0.25,        # how often the VAD thread runs (s)
        vad_grab_sec=0.5,         # audio window VAD thread inspects each tick (s)
        vad_grid_hz=100.0,        # VAD timeline resolution (Hz)
        emo_min_voiced_frac=0.5,  # min voiced fraction of emo_window to fire emotion
        log_interval=0.25,
        display_n=500,
        display_refresh_ms=150,
        osc_ip="127.0.0.1",
        osc_port=9000,
        osc_prefix="/speech",   # appended under /dev/<device_id>/...
        ctrl_port=9001,
        pi_id=None,             # e.g. 5 (int or str); falls back to hostname
        mic_id=None,            # e.g. 1 or 2; falls back to 1
        self_stats_interval=5.0,  # seconds between /stats/self emissions
        emotion_model="base",
        emotion_load=True,
        output_dir="output",
        audio_device=None,   # int index, name substring, or null = system default
    )
    for k, v in defaults.items():
        cfg.setdefault(k, v)

    # CLI overrides
    if args.emotion_model is not None:
        cfg["emotion_model"] = args.emotion_model
    if args.no_emotion_load:
        cfg["emotion_load"] = False
    if args.osc_ip is not None:
        cfg["osc_ip"] = args.osc_ip
    if args.osc_port is not None:
        cfg["osc_port"] = args.osc_port
    if args.osc_prefix is not None:
        cfg["osc_prefix"] = args.osc_prefix
    if args.device is not None:
        cfg["audio_device"] = args.device
    if args.pi_id is not None:
        cfg["pi_id"] = args.pi_id
    if args.mic_id is not None:
        cfg["mic_id"] = args.mic_id
    if args.ctrl_port is not None:
        cfg["ctrl_port"] = args.ctrl_port

    return cfg


ARGS = parse_args()

# Handle --list-devices early (before loading any models)
if ARGS.list_devices:
    list_input_devices()
    sys.exit(0)

CFG = load_config(ARGS)

# ───────────────────────────────────────────────────────────────────
# Device identity. Every outgoing OSC address is rooted under
#   /dev/<DEVICE_ID>/...
# so the Mac collector / bridge can demultiplex many Pis × many mics
# on the same UDP port. DEVICE_ID = "<pi_id>-<mic_id>" (e.g. "5-2").
# ───────────────────────────────────────────────────────────────────
def _resolve_device_id(cfg):
    import socket as _sock
    pi_id = cfg.get("pi_id")
    if pi_id is None or str(pi_id).strip() == "":
        try:
            pi_id = _sock.gethostname().split(".")[0]
        except Exception:
            pi_id = "pi"
    mic_id = cfg.get("mic_id")
    if mic_id is None or str(mic_id).strip() == "":
        mic_id = "1"
    return str(pi_id), str(mic_id), f"{pi_id}-{mic_id}"

PI_ID, MIC_ID, DEVICE_ID = _resolve_device_id(CFG)
DEV_PFX = f"/dev/{DEVICE_ID}"
# Override any user-supplied osc_prefix so every speech topic is namespaced.
CFG["osc_prefix"] = f"{DEV_PFX}/speech"
CTRL_PORT = int(CFG.get("ctrl_port", 9001))
SELF_STATS_INTERVAL_S = float(CFG.get("self_stats_interval", 5.0))
print(f"[ID] device_id={DEVICE_ID}  (pi_id={PI_ID}, mic_id={MIC_ID})  "
      f"ctrl_port={CTRL_PORT}  osc_prefix={CFG['osc_prefix']}")

# Matplotlib — only import if display enabled.
# Disabled when --no-display is passed OR config has `display: false`
# (set this in config.yaml for headless Pi deployments).
_display_enabled = (not ARGS.no_display) and CFG.get("display", True)
if _display_enabled:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.widgets import Button, TextBox


# ═══════════════════════════════════════════════════════════════════
# 1. CONFIG — derived constants and mutable state
# ═══════════════════════════════════════════════════════════════════
SR = int(CFG["sample_rate"])
OUTPUT_DIR = CFG["output_dir"]

# Mutable refs (settable at runtime via config reload or future UI)
_opensmile_interval = [float(CFG["opensmile_interval"])]
_OPENSMILE_MARGIN = float(CFG["opensmile_margin"])
_emo_window = [float(CFG["emo_window"])]
_emo_hop = [float(CFG["emo_hop"])]
_vad_threshold = float(CFG["vad_threshold"])
_vad_interval = [float(CFG["vad_interval"])]
_vad_grab_sec = [float(CFG["vad_grab_sec"])]
_vad_grid_hz = [float(CFG["vad_grid_hz"])]
_emo_min_voiced_frac = [float(CFG["emo_min_voiced_frac"])]
_log_interval = [float(CFG["log_interval"])]
_display_n = [int(CFG["display_n"])]
_display_refresh_ms = int(CFG["display_refresh_ms"])

# Audio buffer: big enough for max consumer + margin
def _compute_buf_sec():
    opensmile_need = _opensmile_interval[0] + _OPENSMILE_MARGIN
    emo_need = _emo_window[0]
    vad_need = _vad_grab_sec[0]
    return max(opensmile_need, emo_need, vad_need) + 2.0

_buf_sec = [_compute_buf_sec()]

# LLD features to extract and stream
FEATURES = [
    ("F0semitoneFrom27.5Hz_sma3nz", "F0 (st)",     "cyan",   True,  (0, 50)),
    ("Loudness_sma3",               "Loudness",     "green",  False, (0, 2.5)),
    ("jitterLocal_sma3nz",          "Jitter",       "pink",   True,  (0, 0.35)),
    ("shimmerLocaldB_sma3nz",       "Shimmer (dB)", "orange", True,  (0, 30)),
    ("HNRdBACF_sma3nz",            "HNR (dB)",     "violet", True,  (0, 15)),
]

EMOTION_DIMS = [
    "angry", "disgusted", "fearful", "happy", "neutral",
    "other", "sad", "surprised", "unknown",
]


# ═══════════════════════════════════════════════════════════════════
# 2. RING BUFFERS (shared between threads)
# ═══════════════════════════════════════════════════════════════════
# Prosody frame buffer — 10ms resolution, ~16 min at 100k entries
_frame_buf = collections.deque(maxlen=100_000)
_frame_lock = threading.Lock()

# Emotion buffer — ~0.5s resolution
_emo_buf = collections.deque(maxlen=10_000)
_emo_lock = threading.Lock()

# VAD timeline buffer — independent of prosody. Entries are
# {"time_s": float, "vad": 0|1} on a fixed grid (default 100 Hz, matching
# the openSMILE LLD rate so any consumer can align trivially).
_vad_buf = collections.deque(maxlen=100_000)
_vad_buf_lock = threading.Lock()
_last_vad_processed_elapsed = [0.0]

# Tracking for incremental openSMILE (only append new frames)
_last_processed_elapsed = [0.0]


# ═══════════════════════════════════════════════════════════════════
# 3. AUDIO ACCUMULATOR
# ═══════════════════════════════════════════════════════════════════
_audio_lock = threading.Lock()
_audio_chunks: list[np.ndarray] = []
# Native input sample rate of the chosen device. Set in main() once the
# device is opened. Defaults to SR so the callback is correct even if
# called before main() finishes init.
_INPUT_SR: int = SR


def _audio_callback(indata, frames, time_info, status):
    mono = indata[:, 0]
    if _INPUT_SR != SR:
        # Resample native_sr -> SR. resample_poly needs integer up/down
        # factors; reduce by gcd so e.g. 48000/16000 -> up=1 down=3.
        from math import gcd
        g = gcd(SR, _INPUT_SR)
        mono = resample_poly(mono, SR // g, _INPUT_SR // g).astype(np.float32)
    else:
        mono = mono.copy()
    with _audio_lock:
        _audio_chunks.append(mono)


def _get_recent_audio(sec: float) -> np.ndarray | None:
    """Return last N seconds of audio from the rolling buffer."""
    with _audio_lock:
        if not _audio_chunks:
            return None
        buf = np.concatenate(_audio_chunks)
        # Trim buffer to max size
        max_samples = int(_buf_sec[0] * SR)
        if len(buf) > max_samples:
            buf = buf[-max_samples:]
            _audio_chunks.clear()
            _audio_chunks.append(buf.copy())
    need = int(sec * SR)
    if len(buf) < int(0.3 * SR):
        return None
    chunk = buf[-need:] if len(buf) >= need else buf
    return chunk.astype(np.float32)


def _get_full_audio() -> np.ndarray | None:
    """Return the full rolling buffer (for waveform display)."""
    with _audio_lock:
        if not _audio_chunks:
            return None
        buf = np.concatenate(_audio_chunks)
        max_samples = int(_buf_sec[0] * SR)
        if len(buf) > max_samples:
            buf = buf[-max_samples:]
            _audio_chunks.clear()
            _audio_chunks.append(buf.copy())
    return buf.astype(np.float32)


# ═══════════════════════════════════════════════════════════════════
# 4. OPENSMILE
# ═══════════════════════════════════════════════════════════════════
smile = opensmile.Smile(
    feature_set=opensmile.FeatureSet.eGeMAPSv02,
    feature_level=opensmile.FeatureLevel.LowLevelDescriptors,
)


# ═══════════════════════════════════════════════════════════════════
# 5. SILERO VAD
# ═══════════════════════════════════════════════════════════════════
print("Loading Silero VAD …")
vad_model, vad_utils = torch.hub.load(
    "snakers4/silero-vad", "silero_vad",
    trust_repo=True, force_reload=False,
)
get_speech_timestamps = vad_utils[0]
print("VAD ready.")

# Lock for VAD MODEL — the model itself is not thread-safe.
# (Distinct from _vad_buf_lock, which guards the timeline buffer.)
_vad_lock = threading.Lock()


# ----- VAD buffer consumers ----------------------------------------
# Other stages NEVER call Silero. They only read from _vad_buf via
# these helpers. This is what makes VAD a true gate independent of
# whether prosody/emotion are running.

def _vad_latest() -> int:
    """Latest VAD state. Tri-state: -1 (VAD off or no data yet),
    0 (gate closed / silence), 1 (gate open / speech)."""
    if not _proc_vad[0]:
        return -1
    with _vad_buf_lock:
        if not _vad_buf:
            return -1
        return int(_vad_buf[-1]["vad"])


def _vad_lookup(times: np.ndarray) -> np.ndarray | None:
    """For each elapsed time in `times`, return the VAD state from the
    timeline (nearest entry at-or-before). Returns None when VAD is off
    or the timeline has no entries covering the request."""
    if not _proc_vad[0]:
        return None
    with _vad_buf_lock:
        n_have = len(_vad_buf)
        if n_have == 0:
            return None
        n_take = min(500, n_have)  # ~5s @ 100 Hz is plenty for any chunk
        recent = list(itertools.islice(
            _vad_buf, n_have - n_take, n_have))
    vt = np.fromiter((v["time_s"] for v in recent), dtype=np.float64,
                     count=len(recent))
    vv = np.fromiter((v["vad"] for v in recent), dtype=np.int8,
                     count=len(recent))
    idx = np.searchsorted(vt, times, side="right") - 1
    idx = np.clip(idx, 0, len(vt) - 1)
    return vv[idx].astype(bool)


def _vad_voiced_fraction(window_sec: float) -> float | None:
    """Fraction of voiced frames in the last `window_sec` of VAD timeline.
    Returns None when VAD is off or no entries cover the window."""
    if not _proc_vad[0]:
        return None
    cutoff = (time.time() - _t_start) - window_sec
    with _vad_buf_lock:
        if not _vad_buf:
            return None
        # Walk from newest backward until we drop below cutoff.
        vals = []
        for entry in reversed(_vad_buf):
            if entry["time_s"] < cutoff:
                break
            vals.append(entry["vad"])
    if not vals:
        return None
    return sum(vals) / len(vals)


# ----- VAD producer thread -----------------------------------------
# Runs at its own period, reads audio directly, writes a 100 Hz
# (configurable) timeline of gate state into _vad_buf. Does NOT
# depend on prosody, emotion, or anything downstream.
def _vad_thread():
    """Independent VAD producer. Inspects `vad_grab_sec` of audio every
    `vad_interval` seconds, projects Silero's speech intervals onto a
    fixed-Hz grid, and appends new entries to _vad_buf."""
    while not _stop_event.is_set():
        time.sleep(_vad_interval[0])
        if not _proc_vad[0]:
            continue

        audio = _get_recent_audio(_vad_grab_sec[0])
        if audio is None or len(audio) < int(0.3 * SR):
            continue

        elapsed_now = time.time() - _t_start
        chunk_duration = len(audio) / SR
        chunk_t0 = elapsed_now - chunk_duration

        tensor = torch.from_numpy(audio).float()
        try:
            with _vad_lock:
                timestamps = get_speech_timestamps(
                    tensor, vad_model,
                    threshold=_vad_threshold, sampling_rate=SR,
                    min_speech_duration_ms=250,
                )
                vad_model.reset_states()
        except Exception as e:
            print(f"[VAD] {e}", file=sys.stderr)
            continue

        # Build a fixed-Hz timeline over the analysed chunk.
        grid_hz = float(_vad_grid_hz[0])
        n_steps = max(1, int(chunk_duration * grid_hz))
        grid_dt = 1.0 / grid_hz
        times_in_audio = np.arange(n_steps) * grid_dt + grid_dt / 2.0
        mask = np.zeros(n_steps, dtype=bool)
        for seg in timestamps:
            t_s = seg["start"] / SR
            t_e = seg["end"] / SR
            mask |= (times_in_audio >= t_s) & (times_in_audio <= t_e)

        frame_elapsed = times_in_audio + chunk_t0

        # Deduplicate against prior ticks (chunks overlap).
        cutoff = _last_vad_processed_elapsed[0]
        new_mask = frame_elapsed > cutoff
        if not np.any(new_mask):
            continue
        new_idx = np.where(new_mask)[0]

        with _vad_buf_lock:
            for i in new_idx:
                _vad_buf.append({
                    "time_s": float(frame_elapsed[i]),
                    "vad": 1 if bool(mask[i]) else 0,
                })
            _last_vad_processed_elapsed[0] = float(frame_elapsed[new_idx[-1]])


# ═══════════════════════════════════════════════════════════════════
# 6. EMOTION MODEL
# ═══════════════════════════════════════════════════════════════════
# Always load the model at startup, regardless of activation state.
# Runtime activation is controlled by _proc_emotion[0] (toggleable via
# /ctrl/emotion_on /ctrl/emotion_off). This guarantees the toggle button
# in the browser is always functional — no asymmetry with VAD/prosody.
_emo_model = None
_emotion_loaded = [False]

model_map = {
    "base": "iic/emotion2vec_plus_base",
    "seed": "iic/emotion2vec_plus_seed",
}
if CFG.get("emotion_load", True):
    from src.emotion_model import Emotion2VecModel

    model_name = model_map[CFG["emotion_model"]]
    print(f"Loading emotion2vec ({CFG['emotion_model']}: {model_name}) …")
    _emo_model = Emotion2VecModel(model_name=model_name, device="cpu")
    _emotion_loaded[0] = True
    print(f"emotion2vec ready — {len(_emo_model.dimensions)} classes")
else:
    print("[EMO] load skipped — emotion model unavailable in this process")


# ═══════════════════════════════════════════════════════════════════
# 7. CSV LOGGING
# ═══════════════════════════════════════════════════════════════════
_log_on = False
_log_file = None
_log_writer = None
_log_t0 = None
_log_path = None
_log_count = [0]
_log_session_start_unix_ms = [""]
_log_session_start_iso = [""]
_log_start_at_unix_ms = [""]
_log_start_at_iso = [""]
_log_start_scheduled = [False]
_log_schedule_token = [0]

# Recording timer (accumulates across pause/resume; resets on RESET)
_rec_elapsed = [0.0]      # accumulated recording seconds
_rec_resume_t = [0.0]     # time.time() when current session started
_rec_total_rows = [0]     # total rows written since last reset


def _log_session_open() -> bool:
    return _log_file is not None and _log_writer is not None


def _log_session_paused() -> bool:
    return _log_session_open() and not _log_on and not _log_start_scheduled[0]


def _log_session_scheduled() -> bool:
    return _log_session_open() and _log_start_scheduled[0]


def _current_recorded_secs() -> float:
    if _log_on:
        return _rec_elapsed[0] + (time.time() - _rec_resume_t[0])
    return _rec_elapsed[0]


def _normalise_session_start(session_start_unix_ms=None, session_start_iso=None):
    if session_start_unix_ms is None or str(session_start_unix_ms).strip() == "":
        unix_ms = str(int(time.time() * 1000))
    else:
        try:
            unix_ms = str(int(float(session_start_unix_ms)))
        except (TypeError, ValueError):
            unix_ms = str(session_start_unix_ms)

    if session_start_iso is None or str(session_start_iso).strip() == "":
        iso = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
    else:
        iso = str(session_start_iso)

    return unix_ms, iso


def _normalise_start_at(start_at_unix_ms=None, start_at_iso=None,
                        default_unix_ms=None, default_iso=None):
    if start_at_unix_ms is None or str(start_at_unix_ms).strip() == "":
        unix_ms = str(default_unix_ms if default_unix_ms is not None else int(time.time() * 1000))
    else:
        try:
            unix_ms = str(int(float(start_at_unix_ms)))
        except (TypeError, ValueError):
            unix_ms = str(start_at_unix_ms)

    if start_at_iso is None or str(start_at_iso).strip() == "":
        if default_iso is not None and unix_ms == str(default_unix_ms):
            iso = str(default_iso)
        else:
            iso = datetime.utcfromtimestamp(int(unix_ms) / 1000.0).isoformat(timespec="milliseconds") + "Z"
    else:
        iso = str(start_at_iso)

    return unix_ms, iso


def _schedule_log_activation(start_at_unix_ms: str, start_at_iso: str):
    _log_schedule_token[0] += 1
    token = _log_schedule_token[0]
    _log_start_scheduled[0] = True
    target_unix_ms = int(start_at_unix_ms)

    def _wait_and_activate(local_token: int, target_ms: int, target_iso_value: str):
        global _log_on
        while True:
            if local_token != _log_schedule_token[0] or not _log_session_open() or _log_on:
                return
            delay_s = (target_ms / 1000.0) - time.time()
            if delay_s <= 0:
                break
            if _stop_event.wait(min(delay_s, 0.05)):
                return

        if local_token != _log_schedule_token[0] or not _log_session_open() or _log_on:
            return

        _log_start_scheduled[0] = False
        _rec_resume_t[0] = time.time()
        _log_on = True
        print(f"[LOG] scheduled start reached → {_log_path}  start_at={target_iso_value} ({target_ms})")
        _emit_state()

    threading.Thread(
        target=_wait_and_activate,
        args=(token, target_unix_ms, start_at_iso),
        daemon=True,
        name="log_schedule",
    ).start()


def _csv_header():
    cols = ["session_start_unix_ms", "session_start_iso", "time_ms", "vad"]
    cols += [f[0] for f in FEATURES]
    # Emotion model is always loaded → columns always present. Rows written
    # while emotion is toggled OFF will leave these blank.
    cols += ["emo_label", "emo_confidence"] + EMOTION_DIMS
    return cols


def log_start(path: str | None = None, session_start_unix_ms=None,
              session_start_iso=None, start_at_unix_ms=None,
              start_at_iso=None):
    global _log_on, _log_file, _log_writer, _log_t0, _log_path
    if _log_session_open():
        log_stop(reason="replaced")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(OUTPUT_DIR, f"track_{ts}.csv")
    _log_path = path
    _log_session_start_unix_ms[0], _log_session_start_iso[0] = _normalise_session_start(
        session_start_unix_ms, session_start_iso
    )
    _log_start_at_unix_ms[0], _log_start_at_iso[0] = _normalise_start_at(
        start_at_unix_ms,
        start_at_iso,
        default_unix_ms=int(_log_session_start_unix_ms[0]),
        default_iso=_log_session_start_iso[0],
    )
    _log_file = open(path, "w", newline="")
    _log_writer = csv.writer(_log_file)
    _log_writer.writerow(_csv_header())
    _log_t0 = time.time()
    _log_count[0] = 0
    _rec_elapsed[0] = 0.0
    _rec_total_rows[0] = 0
    _log_on = False
    now_unix_ms = int(time.time() * 1000)
    start_at_ms = int(_log_start_at_unix_ms[0])
    if start_at_ms <= now_unix_ms:
        _log_start_scheduled[0] = False
        _rec_resume_t[0] = time.time()
        _log_on = True
        print(
            f"[LOG] new file → {path}  "
            f"session_start={_log_session_start_iso[0]} ({_log_session_start_unix_ms[0]})"
        )
    else:
        _log_start_scheduled[0] = True
        print(
            f"[LOG] new file scheduled → {path}  "
            f"session_start={_log_session_start_iso[0]} ({_log_session_start_unix_ms[0]})  "
            f"start_at={_log_start_at_iso[0]} ({_log_start_at_unix_ms[0]})"
        )
        _schedule_log_activation(_log_start_at_unix_ms[0], _log_start_at_iso[0])
    _emit_state()


def log_pause():
    global _log_on
    if not _log_on:
        if _log_session_scheduled():
            print(f"[LOG] session is scheduled, not yet active → {_log_path}")
            return
        if _log_session_paused():
            print(f"[LOG] already paused → {_log_path}")
        else:
            print("[LOG] pause requested but no active session is running")
        return
    _rec_elapsed[0] += time.time() - _rec_resume_t[0]
    _log_on = False
    if _log_file:
        _log_file.flush()
    print(f"[LOG] paused → {_log_path}  ({_log_count[0]} rows)")
    _emit_state()


def log_resume():
    global _log_on
    if _log_on:
        print(f"[LOG] already running → {_log_path}")
        return
    if _log_session_scheduled():
        print(f"[LOG] session already scheduled → {_log_start_at_iso[0]} ({_log_start_at_unix_ms[0]})")
        return
    if not _log_session_open():
        print("[LOG] resume requested but no paused session exists")
        return
    _rec_resume_t[0] = time.time()
    _log_on = True
    print(f"[LOG] resumed → {_log_path}")
    _emit_state()


def log_stop(reason: str = "stopped"):
    global _log_on, _log_file, _log_writer, _log_t0
    if not _log_session_open():
        return
    _log_schedule_token[0] += 1
    _log_start_scheduled[0] = False
    if _log_on:
        _rec_elapsed[0] += time.time() - _rec_resume_t[0]
    _log_on = False
    if _log_file:
        try:
            _log_file.flush()
        except Exception:
            pass
        _log_file.close()
        print(f"[LOG] {reason} → {_log_path}  ({_log_count[0]} rows)")
    _log_file = _log_writer = _log_t0 = None
    _emit_state()


# ═══════════════════════════════════════════════════════════════════
# 8. OSC STREAMING
# ═══════════════════════════════════════════════════════════════════
_osc_on = False
_osc_client = None

# Per-address send-rate tracking (msg timestamps per OSC address)
_osc_rate_lock = threading.Lock()
_osc_rate_history: dict[str, list[float]] = {}
_OSC_RATE_WINDOW = 2.0  # seconds for Hz estimate


def osc_start(ip: str = None, port: int = None):
    global _osc_on, _osc_client
    ip = ip or CFG["osc_ip"]
    port = port or CFG["osc_port"]
    try:
        from pythonosc.udp_client import SimpleUDPClient
        _osc_client = SimpleUDPClient(ip, port)
        _osc_on = True
        print(f"[OSC] streaming → {ip}:{port}")
    except ImportError:
        print("[OSC] python-osc not installed. Run: pip install python-osc")
        _osc_on = False
    _emit_state()


def osc_stop():
    global _osc_on
    if not _osc_on:
        return
    _osc_on = False
    print("[OSC] paused")
    # Notify browser BEFORE we silence further sends. _emit_state() bypasses
    # the _osc_on gate so it still goes out. Client is kept alive so a future
    # osc_start() resumes streaming without renegotiating anything.
    _emit_state()


def _emit(addr: str, vals):
    """Send one OSC message and record timestamp for rate tracking.
    Safe to call when OSC is off (becomes a no-op)."""
    if not _osc_on or _osc_client is None:
        return
    try:
        _osc_client.send_message(addr, vals)
    except Exception as e:
        print(f"[OSC] {e}", file=sys.stderr)
        return
    now = time.time()
    with _osc_rate_lock:
        hist = _osc_rate_history.setdefault(addr, [])
        hist.append(now)
        cutoff = now - _OSC_RATE_WINDOW
        while hist and hist[0] < cutoff:
            hist.pop(0)


def _emit_state():
    """Broadcast all control/processing flags so the browser UI can sync.
    Bypasses the _osc_on gate — as long as the client exists we can
    notify the browser even while streaming is paused."""
    if _osc_client is None:
        return
    try:
        _osc_client.send_message(f"{DEV_PFX}/state/osc_active", [1 if _osc_on else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/log_active", [1 if _log_on else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/log_session_open", [1 if _log_session_open() else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/log_paused", [1 if _log_session_paused() else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/log_scheduled", [1 if _log_session_scheduled() else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/vad_active", [1 if _proc_vad[0] else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/emotion_active", [1 if _proc_emotion[0] else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/emotion_loaded", [1 if _emotion_loaded[0] else 0])
        _osc_client.send_message(f"{DEV_PFX}/state/prosody_active", [1 if _proc_prosody[0] else 0])
    except Exception as e:
        print(f"[OSC] state emit: {e}", file=sys.stderr)


def _set_proc(flag_ref, name: str, on: bool):
    """Toggle a processing stage at runtime and notify the browser."""
    flag_ref[0] = bool(on)
    print(f"[PROC] {name} = {'ON' if on else 'OFF'}")
    _emit_state()


def _set_emotion_proc(on: bool):
    if on and not _emotion_loaded[0]:
        print("[PROC] EMOTION unavailable — model not loaded")
        _proc_emotion[0] = False
        _emit_state()
        return
    _set_proc(_proc_emotion, "EMOTION", on)


def _ctrl_log_start(addr, *args):
    session_start_unix_ms = args[0] if len(args) >= 1 else None
    session_start_iso = args[1] if len(args) >= 2 else None
    start_at_unix_ms = args[2] if len(args) >= 3 else None
    start_at_iso = args[3] if len(args) >= 4 else None
    log_start(session_start_unix_ms=session_start_unix_ms,
              session_start_iso=session_start_iso,
              start_at_unix_ms=start_at_unix_ms,
              start_at_iso=start_at_iso)


def _osc_send(vad_speech: int, feature_means: dict, emo_scores: dict):
    if not _osc_on or _osc_client is None:
        return
    pfx = CFG["osc_prefix"]
    _emit(f"{pfx}/vad", [float(vad_speech)])
    for key, _, _, _, _ in FEATURES:
        v = feature_means.get(key, 0.0)
        val = float(v) if not np.isnan(v) else 0.0
        _emit(f"{pfx}/{key}", [val])
    if _proc_emotion[0] and emo_scores:
        top_dim = max(emo_scores, key=emo_scores.get) if emo_scores else ""
        top_val = emo_scores.get(top_dim, 0.0)
        _emit(f"{pfx}/emo/label", [top_dim, float(top_val)])
        vals = [float(emo_scores.get(d, 0.0)) for d in EMOTION_DIMS]
        _emit(f"{pfx}/emo/scores", vals)


def _rate_stats_thread():
    """Every 2 s: compute per-address Hz, print to stdout, emit /stats/rate.
    Runs even when OSC is off (stdout still useful for headless debug)."""
    while not _stop_event.is_set():
        time.sleep(_OSC_RATE_WINDOW)
        now = time.time()
        cutoff = now - _OSC_RATE_WINDOW
        snapshot = {}
        with _osc_rate_lock:
            for addr, hist in _osc_rate_history.items():
                while hist and hist[0] < cutoff:
                    hist.pop(0)
                snapshot[addr] = len(hist) / _OSC_RATE_WINDOW
        if not snapshot:
            continue
        parts = [f"{a.rsplit('/', 1)[-1]}={hz:.1f}" for a, hz in sorted(snapshot.items())]
        print(f"[RATE {DEVICE_ID}] " + "  ".join(parts))
        for addr, hz in snapshot.items():
            _emit(f"{DEV_PFX}/stats/rate", [addr, float(hz)])


# ═══════════════════════════════════════════════════════════════════
# 8b. REMOTE CONTROL LISTENER (OSC on CTRL_PORT)
# ═══════════════════════════════════════════════════════════════════
def _start_ctrl_listener():
    try:
        from pythonosc.dispatcher import Dispatcher
        from pythonosc.osc_server import BlockingOSCUDPServer
    except ImportError:
        print("[CTRL] python-osc not installed — remote control disabled")
        return

    disp = Dispatcher()
    # /ctrl/osc_start auto-routes the OSC stream back to whoever sent the
    # CTRL packet (the bridge). With needs_reply_address=True python-osc
    # passes (client_addr_tuple, osc_addr, *args) to the handler. This
    # eliminates any need to pre-configure the Mac's IP on the Pi.
    disp.map("/ctrl/osc_start",
             lambda client, addr, *a: osc_start(ip=client[0]),
             needs_reply_address=True)
    disp.map("/ctrl/osc_stop", lambda addr, *a: osc_stop())
    disp.map("/ctrl/log_start", _ctrl_log_start)
    disp.map("/ctrl/log_pause", lambda addr, *a: log_pause())
    disp.map("/ctrl/log_resume", lambda addr, *a: log_resume())
    disp.map("/ctrl/log_stop", lambda addr, *a: log_stop())
    disp.map("/ctrl/query_state", lambda addr, *a: _emit_state())
    # Per-stage processing toggles
    disp.map("/ctrl/vad_on",       lambda addr, *a: _set_proc(_proc_vad,      "VAD",     True))
    disp.map("/ctrl/vad_off",      lambda addr, *a: _set_proc(_proc_vad,      "VAD",     False))
    disp.map("/ctrl/emotion_on",   lambda addr, *a: _set_emotion_proc(True))
    disp.map("/ctrl/emotion_off",  lambda addr, *a: _set_emotion_proc(False))
    disp.map("/ctrl/prosody_on",   lambda addr, *a: _set_proc(_proc_prosody,  "PROSODY", True))
    disp.map("/ctrl/prosody_off",  lambda addr, *a: _set_proc(_proc_prosody,  "PROSODY", False))

    try:
        server = BlockingOSCUDPServer(("0.0.0.0", CTRL_PORT), disp)
        print(f"[CTRL] listening on :{CTRL_PORT}  (device {DEVICE_ID})")
        server.serve_forever()
    except OSError as e:
        print(f"[CTRL] port {CTRL_PORT} unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════
# 8c. DISCOVERY HEARTBEAT (UDP broadcast)
# ═══════════════════════════════════════════════════════════════════
# Periodically broadcasts a /hello packet so any bridge on the LAN
# learns this Pi's IP without any per-Pi configuration. Runs
# independently of OSC streaming on/off — the bridge needs to know
# we exist before the user can click the OSC button.
HELLO_INTERVAL_S = 2.0
HELLO_VERSION = "2"   # bumped: payload now carries device_id + ctrl_port


def _hostname_short() -> str:
    import socket as _socket
    try:
        return _socket.gethostname().split(".")[0]
    except Exception:
        return "pi"


def _hello_thread():
    import socket as _socket
    try:
        from pythonosc.osc_message_builder import OscMessageBuilder
    except ImportError:
        print("[HELLO] python-osc not installed — discovery disabled")
        return

    port = int(CFG.get("osc_port", 9000))
    hostname = _hostname_short()

    # Build once — payload is static for the lifetime of this process.
    # v2 schema: device_id, pi_id, mic_id, hostname, ctrl_port, version
    b = OscMessageBuilder(address="/hello")
    b.add_arg(DEVICE_ID)
    b.add_arg(PI_ID)
    b.add_arg(MIC_ID)
    b.add_arg(hostname)
    b.add_arg(int(CTRL_PORT))
    b.add_arg(HELLO_VERSION)
    packet = b.build().dgram

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
    except Exception as e:
        print(f"[HELLO] SO_BROADCAST failed: {e}", file=sys.stderr)

    target = ("255.255.255.255", port)
    print(f"[HELLO] broadcasting /hello {DEVICE_ID} → {target[0]}:{target[1]} "
          f"every {HELLO_INTERVAL_S:g}s")

    while not _stop_event.is_set():
        try:
            sock.sendto(packet, target)
        except Exception as e:
            print(f"[HELLO] {e}", file=sys.stderr)
        _stop_event.wait(HELLO_INTERVAL_S)


# ════════════════════════════════════════════════════════════════════════
# 8d. SELF-TELEMETRY (RSS / CPU% / temp) — emits /dev/<id>/stats/self
# ════════════════════════════════════════════════════════════════════════
# Lets us measure how much pressure each strip_monitor instance puts
# on the Pi — critical when running two instances per Pi (one per mic).
# Always prints to stdout (useful headless); emits OSC only when the
# stream is up. psutil is optional: if missing, the thread quietly exits.
def _read_pi_temp_c():
    """Read CPU temperature in Celsius. Tries /sys then vcgencmd. None on failure."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return float(f.read().strip()) / 1000.0
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.check_output(["vcgencmd", "measure_temp"], timeout=0.5).decode()
        # "temp=58.4'C\n"
        return float(out.split("=")[1].split("'")[0])
    except Exception:
        return None


def _self_stats_thread():
    try:
        import psutil
    except ImportError:
        print("[SELF] psutil not installed — self-telemetry disabled "
              "(pip install psutil)")
        return
    proc = psutil.Process(os.getpid())
    # Prime cpu_percent so the first real reading is meaningful.
    proc.cpu_percent(interval=None)
    n_cores = psutil.cpu_count(logical=True) or 1
    while not _stop_event.is_set():
        _stop_event.wait(SELF_STATS_INTERVAL_S)
        if _stop_event.is_set():
            break
        try:
            rss_mb = proc.memory_info().rss / (1024 * 1024)
            # cpu_percent returns process %, can exceed 100 on multi-core.
            # Normalise to "% of total machine" for easier comparison.
            cpu_pct_total = proc.cpu_percent(interval=None) / n_cores
            n_threads = proc.num_threads()
            temp_c = _read_pi_temp_c()
        except Exception as e:
            print(f"[SELF] {e}", file=sys.stderr)
            continue
        temp_str = f"{temp_c:.1f}C" if temp_c is not None else "n/a"
        print(f"[SELF {DEVICE_ID}] rss={rss_mb:.0f}MB "
              f"cpu={cpu_pct_total:.0f}% temp={temp_str} "
              f"threads={n_threads}")
        # Emit as OSC (no-op when stream is off).
        _emit(f"{DEV_PFX}/stats/self",
              [float(rss_mb),
               float(cpu_pct_total),
               float(temp_c) if temp_c is not None else -1.0,
               int(n_threads)])


# ═══════════════════════════════════════════════════════════════════
# 9. SHARED STATE
# ═══════════════════════════════════════════════════════════════════
_stop_event = threading.Event()
_t_start = time.time()
_stream: sd.InputStream | None = None

# Processing toggles (initial state from config, togglable at runtime).
# CLI --no-* flags force-disable the corresponding stage regardless of config.
_proc_vad = [CFG.get("vad_active", True) and not ARGS.no_vad]
_proc_emotion = [CFG.get("emotion_active", True) and not ARGS.no_emotion and _emotion_loaded[0]]
_proc_prosody = [CFG.get("prosody_active", True) and not ARGS.no_prosody]
_display_on = [True]

if not _emotion_loaded[0] and CFG.get("emotion_active", True) and not ARGS.no_emotion:
    print("[EMO] processing forced OFF because the model was not loaded")

# Rate tracking
_opensmile_rate = [0.0]   # actual frames/sec produced
_emo_rate = [0.0]         # actual predictions/sec
_logger_rate = [0.0]      # actual logger ticks/sec (data output rate)


# ═══════════════════════════════════════════════════════════════════
# 10. OPENSMILE PROCESSING THREAD
# ═══════════════════════════════════════════════════════════════════
def _opensmile_thread():
    """Process short audio windows and append new 10ms frames to _frame_buf."""
    last_call = time.time()
    frames_produced = 0
    rate_window_start = time.time()

    while not _stop_event.is_set():
        time.sleep(_opensmile_interval[0])
        if not _proc_prosody[0]:
            continue

        # Grab short window: interval + margin
        grab_sec = _opensmile_interval[0] + _OPENSMILE_MARGIN
        audio = _get_recent_audio(grab_sec)
        if audio is None or len(audio) < int(0.3 * SR):
            continue

        elapsed_now = time.time() - _t_start
        chunk_duration = len(audio) / SR

        # Run openSMILE on the short chunk
        try:
            df = smile.process_signal(audio, sampling_rate=SR)
        except Exception as e:
            print(f"[openSMILE] {e}", file=sys.stderr)
            continue

        if df is None or len(df) == 0:
            continue

        # Compute frame times in elapsed wall-clock coordinates
        starts = np.array([t.total_seconds() for t in df.index.get_level_values("start")])
        ends = np.array([t.total_seconds() for t in df.index.get_level_values("end")])
        times_in_audio = (starts + ends) / 2.0
        # Convert audio-relative → elapsed: audio ends at elapsed_now
        frame_elapsed = times_in_audio + (elapsed_now - chunk_duration)

        # VAD: pull state from the independent VAD timeline buffer.
        # When VAD is off OR the buffer has no entries yet, _vad_lookup
        # returns None and every frame is treated as gate-open (vad=-1).
        speech_mask = _vad_lookup(frame_elapsed)

        # Only keep NEW frames (beyond what we already processed)
        cutoff = _last_processed_elapsed[0]
        new_mask = frame_elapsed > cutoff
        if not np.any(new_mask):
            continue

        new_indices = np.where(new_mask)[0]
        new_count = 0

        with _frame_lock:
            for idx in new_indices:
                t = float(frame_elapsed[idx])
                # Tri-state VAD: -1 = VAD off, 0 = gate closed, 1 = gate open
                if speech_mask is None:
                    vad = -1
                    vad_open = True  # treat as open for feature gating
                else:
                    vad = 1 if bool(speech_mask[idx]) else 0
                    vad_open = (vad == 1)
                entry = {"time_s": t, "vad": vad}
                for key, _, _, is_nz, _ in FEATURES:
                    val = float(df.iloc[idx][key])
                    if is_nz and val <= 0:
                        val = float("nan")
                    if is_nz and not vad_open:
                        val = float("nan")
                    entry[key] = val
                _frame_buf.append(entry)
                new_count += 1

            if new_count > 0:
                _last_processed_elapsed[0] = float(frame_elapsed[new_indices[-1]])

        # Rate tracking
        frames_produced += new_count
        dt = time.time() - rate_window_start
        if dt >= 2.0:
            _opensmile_rate[0] = frames_produced / dt
            frames_produced = 0
            rate_window_start = time.time()


# ═══════════════════════════════════════════════════════════════════
# 11. EMOTION PROCESSING THREAD (sliding window)
# ═══════════════════════════════════════════════════════════════════
def _emotion_thread():
    """Sliding window emotion inference → _emo_buf."""
    preds_produced = 0
    rate_window_start = time.time()
    model = _emo_model

    if model is None:
        print("[EMO] thread not started — model unavailable", file=sys.stderr)
        return

    while not _stop_event.is_set():
        time.sleep(_emo_hop[0])
        if not _proc_emotion[0]:
            continue

        audio = _get_recent_audio(_emo_window[0])
        if audio is None:
            continue

        # Gate on VAD activity over the same window we're about to feed
        # to the model. _vad_voiced_fraction returns None when VAD is
        # off or has no data yet → gate is open (no filtering).
        voiced_frac = _vad_voiced_fraction(_emo_window[0])
        if voiced_frac is not None and voiced_frac < _emo_min_voiced_frac[0]:
            continue

        elapsed_now = time.time() - _t_start

        try:
            r = model.predict(audio, sr=SR)
        except Exception as e:
            print(f"[EMO] {e}", file=sys.stderr)
            continue

        scores = r.get("scores", {})
        label = r.get("label", "")
        confidence = r.get("confidence", 0.0)

        entry = {
            "time_s": elapsed_now,
            "label": label,
            "confidence": confidence,
            "scores": {d: float(scores.get(d, 0.0)) for d in EMOTION_DIMS},
        }

        with _emo_lock:
            _emo_buf.append(entry)

        preds_produced += 1
        dt = time.time() - rate_window_start
        if dt >= 2.0:
            _emo_rate[0] = preds_produced / dt
            preds_produced = 0
            rate_window_start = time.time()


# ═══════════════════════════════════════════════════════════════════
# 12. LOGGER THREAD
# ═══════════════════════════════════════════════════════════════════
def _logger_thread():
    """Write CSV rows and send OSC at configurable rate."""
    last_frame = None
    last_emo = {}
    ticks_produced = 0
    rate_window_start = time.time()

    while not _stop_event.is_set():
        time.sleep(_log_interval[0])
        ticks_produced += 1
        dt = time.time() - rate_window_start
        if dt >= 2.0:
            _logger_rate[0] = ticks_produced / dt
            ticks_produced = 0
            rate_window_start = time.time()

        # Read latest prosody frame
        with _frame_lock:
            if _frame_buf:
                last_frame = _frame_buf[-1].copy()

        # Read latest emotion
        with _emo_lock:
            if _emo_buf:
                last_emo = _emo_buf[-1].copy()

        # Read latest VAD state from its own timeline (independent of prosody)
        vad = _vad_latest()  # tri-state: -1/0/1

        # NOTE: do NOT early-continue here. The logger ticks on its own
        # grid and emits OSC/CSV regardless of whether upstream stages
        # are active — missing features become NaN/0, missing emotion
        # is omitted. This is what lets the bridge see a live stream as
        # soon as OSC is enabled, even with prosody/emotion/VAD all off.

        now = time.time()
        feature_means = {key: (last_frame.get(key, float("nan"))
                                if last_frame is not None else float("nan"))
                         for key, _, _, _, _ in FEATURES}
        emo_scores = dict(last_emo.get("scores", {}))

        # Apply zero-on-silence based on recent VAD timeline only.
        if CFG.get("emo_zero_on_silence", False):
            recent_voiced = _vad_voiced_fraction(0.2)
            if recent_voiced is not None and recent_voiced == 0.0:
                emo_scores = {d: 0.0 for d in EMOTION_DIMS}

        # Apply exponential decay based on age of last prediction
        if CFG.get("emo_decay_active", False) and emo_scores:
            tau = CFG.get("emo_decay_tau", 2.0)
            emo_time = last_emo.get("time_s", 0.0)
            age = (now - _t_start) - emo_time
            if age > 0 and tau > 0:
                decay = np.exp(-age / tau)
                emo_scores = {d: v * decay for d, v in emo_scores.items()}

        # CSV
        if _log_on and _log_writer is not None:
            ms = int(_current_recorded_secs() * 1000)
            row = [
                _log_session_start_unix_ms[0],
                _log_session_start_iso[0],
                ms,
                vad,
            ]  # tri-state: -1 (VAD off), 0 (silent), 1 (speech)
            for key, _, _, _, _ in FEATURES:
                v = feature_means.get(key, float("nan"))
                row.append(f"{v:.4f}" if not np.isnan(v) else "")
            if _proc_emotion[0]:
                label = last_emo.get("label", "")
                conf = last_emo.get("confidence", 0.0)
                row.append(label)
                row.append(f"{conf:.4f}" if conf else "")
                for d in EMOTION_DIMS:
                    v = emo_scores.get(d, 0.0)
                    row.append(f"{v:.4f}")
            else:
                # Keep column count stable when emotion is toggled off.
                row.append("")
                row.append("")
                row.extend("" for _ in EMOTION_DIMS)
            _log_writer.writerow(row)
            _log_count[0] += 1
            _rec_total_rows[0] += 1

        # OSC (receives processed scores — decay/zeroing already applied)
        _osc_send(vad, feature_means, emo_scores)


# ═══════════════════════════════════════════════════════════════════
# 13. DISPLAY (optional)
# ═══════════════════════════════════════════════════════════════════
if _display_enabled:
    from matplotlib.gridspec import GridSpec
    from matplotlib.patches import Polygon

    EMOTION_COLORS_MAP = {
        "angry":     "#FF4444",
        "disgusted": "#88AA00",
        "fearful":   "#AA44FF",
        "happy":     "#FFD700",
        "neutral":   "#4488FF",
        "other":     "#888888",
        "sad":       "#6688CC",
        "surprised": "#FF8800",
        "unknown":   "#666666",
    }

    n_feature_strips = len(FEATURES)
    # Model is always loaded, so the display panel is always available.
    _has_emo_display = True

    # ── Build layout ──
    height_ratios = []
    if _has_emo_display:
        height_ratios.append(2.5)                     # emotion bars
    height_ratios += [2, 1] + [1] * n_feature_strips  # wave + VAD + features
    n_rows = len(height_ratios)

    fig = plt.figure(figsize=(12, 1.2 * n_rows + 1.0))
    fig.patch.set_facecolor("#1a1a2e")

    gs = GridSpec(n_rows, 1, figure=fig,
                  height_ratios=height_ratios,
                  left=0.14, right=0.98, top=0.94, bottom=0.10,
                  hspace=0.35)

    row_idx = 0

    # ── Emotion bar chart (vertical) ──
    ax_emo = None
    emo_bars = None
    if _has_emo_display:
        ax_emo = fig.add_subplot(gs[row_idx]); row_idx += 1
        ax_emo.set_facecolor("#1a1a2e")
        emo_x = np.arange(len(EMOTION_DIMS))
        emo_colors = [EMOTION_COLORS_MAP.get(d, "#888") for d in EMOTION_DIMS]
        emo_bars = ax_emo.bar(emo_x, [0] * len(EMOTION_DIMS),
                              color=emo_colors, width=0.75, alpha=0.7)
        ax_emo.set_xticks(emo_x)
        ax_emo.set_xticklabels([d.capitalize() for d in EMOTION_DIMS],
                               fontsize=6, color="white", rotation=0)
        ax_emo.set_ylim(0, 1.0)
        ax_emo.tick_params(axis="y", colors="gray", labelsize=6)
        ax_emo.tick_params(axis="x", length=0)
        for spine in ax_emo.spines.values():
            spine.set_color("#333")
        ax_emo.grid(True, axis="y", alpha=0.15, color="gray")

    # ── Waveform ──
    ax_wave = fig.add_subplot(gs[row_idx]); row_idx += 1
    ax_wave.set_facecolor("#1a1a2e")
    ax_wave.set_ylabel("Wave", color="white", fontsize=8)
    ax_wave.set_ylim(-0.1, 0.1)
    ax_wave.tick_params(colors="gray", labelsize=7)
    line_wave, = ax_wave.plot([], [], color="gray", linewidth=0.3)

    # ── VAD (binary filled) ──
    ax_vad = fig.add_subplot(gs[row_idx], sharex=ax_wave); row_idx += 1
    ax_vad.set_facecolor("#1a1a2e")
    ax_vad.set_ylabel("VAD", color="#88FF88", fontsize=8)
    ax_vad.set_ylim(-0.05, 1.15)
    ax_vad.set_yticks([0, 1])
    ax_vad.set_yticklabels(["sil", "spk"], fontsize=6, color="#88FF88")
    ax_vad.tick_params(colors="gray", labelsize=7)
    _vad_poly = Polygon(np.zeros((1, 2)), closed=True,
                        facecolor="#88FF88", alpha=0.5, edgecolor="none")
    ax_vad.add_patch(_vad_poly)

    # ── Feature strips ──
    lines = []
    feature_axes = []
    for i, (key, label, color, is_nz, ylim) in enumerate(FEATURES):
        ax = fig.add_subplot(gs[row_idx], sharex=ax_wave); row_idx += 1
        ax.set_facecolor("#1a1a2e")
        ax.set_ylabel(label, color=color, fontsize=8)
        ax.set_ylim(ylim)
        ax.tick_params(colors="gray", labelsize=7)
        ln, = ax.plot([], [], color=color, linewidth=1.5)
        lines.append(ln)
        feature_axes.append(ax)

    all_ts_axes = [ax_wave, ax_vad] + feature_axes
    for ax in all_ts_axes:
        ax.set_xlim(0, _display_n[0] * 0.01)
        ax.grid(True, alpha=0.15, color="gray")
        for spine in ax.spines.values():
            spine.set_color("#333")
    feature_axes[-1].set_xlabel("Time (s)", color="white", fontsize=9)

    # ── Toggle buttons (left margin) ──
    _btn_widgets = []

    _toggle_specs = []
    if _has_emo_display:
        _toggle_specs.append(("EMO", _proc_emotion, "#442200", "#886600"))
    _toggle_specs += [
        ("VAD", _proc_vad,     "#1a331a", "#338833"),
        ("PRS", _proc_prosody, "#1a2233", "#336688"),
        ("DSP", _display_on,   "#1a1a33", "#334466"),
    ]

    def _make_toggle_cb(flag, btn_ref, name, off_c, on_c):
        def cb(event):
            flag[0] = not flag[0]
            b = btn_ref[0]
            if flag[0]:
                b.label.set_text(f"■ {name}")
                b.color = on_c
                b.hovercolor = on_c
            else:
                b.label.set_text(f"○ {name}")
                b.color = off_c
                b.hovercolor = off_c
        return cb

    n_tgl = len(_toggle_specs)
    for idx, (name, flag_ref, off_color, on_color) in enumerate(_toggle_specs):
        y_pos = 0.88 - idx * (0.68 / max(n_tgl - 1, 1))
        ax_btn = fig.add_axes([0.015, y_pos, 0.06, 0.035])
        ax_btn.set_facecolor(on_color)
        btn = Button(ax_btn, f"■ {name}", color=on_color, hovercolor=on_color)
        btn.label.set_fontsize(7)
        btn.label.set_color("white")
        btn_ref = [btn]
        btn.on_clicked(_make_toggle_cb(flag_ref, btn_ref, name, off_color, on_color))
        _btn_widgets.append(btn)

    # ── Bottom control bar ──
    # LOG toggle
    ax_log = fig.add_axes([0.08, 0.02, 0.10, 0.04])
    btn_log = Button(ax_log, "START LOG", color="#444", hovercolor="#444")
    btn_log.label.set_fontsize(8)
    _log_btn_state = [False]

    def _toggle_log(event):
        if _log_btn_state[0]:
            log_stop()
            btn_log.label.set_text("START LOG")
            btn_log.color = "#444"
            btn_log.hovercolor = "#444"
            _log_btn_state[0] = False
        else:
            log_start()
            btn_log.label.set_text("STOP LOG")
            btn_log.color = "#CC4444"
            btn_log.hovercolor = "#CC4444"
            _log_btn_state[0] = True
    btn_log.on_clicked(_toggle_log)
    _btn_widgets.append(btn_log)

    # RESET LOG — stop current log, start a fresh one (new file)
    ax_reset = fig.add_axes([0.19, 0.02, 0.08, 0.04])
    btn_reset = Button(ax_reset, "↺ RESET", color="#555", hovercolor="#555")
    btn_reset.label.set_fontsize(8)

    def _reset_log(event):
        if not _log_btn_state[0]:
            print("[LOG] not logging — nothing to reset")
            return
        log_stop()
        _rec_elapsed[0] = 0.0
        _rec_total_rows[0] = 0
        log_start()
        print("[LOG] reset — new file started")
    btn_reset.on_clicked(_reset_log)
    _btn_widgets.append(btn_reset)

    # SAVE AS
    ax_save = fig.add_axes([0.28, 0.02, 0.08, 0.04])
    btn_save = Button(ax_save, "SAVE AS", color="#666", hovercolor="#666")
    btn_save.label.set_fontsize(8)

    # Recording timer label (between SAVE AS and OSC)
    ax_rec_timer = fig.add_axes([0.37, 0.02, 0.12, 0.04])
    ax_rec_timer.set_facecolor("#1a1a2e")
    ax_rec_timer.axis("off")
    _rec_timer_text = ax_rec_timer.text(
        0.5, 0.5, "", color="#888888", fontsize=8,
        ha="center", va="center", transform=ax_rec_timer.transAxes,
        family="monospace")

    def _save_as(event):
        import tkinter as tk
        from tkinter import filedialog
        import shutil

        # Determine source file: current log (if recording) or last log
        if _log_btn_state[0]:
            # Currently recording — stop first to flush
            log_stop()
            btn_log.label.set_text("START LOG")
            btn_log.color = "#444"
            btn_log.hovercolor = "#444"
            _log_btn_state[0] = False
        src = _log_path
        if not src or not os.path.exists(src):
            print("[SAVE] No log file to save.")
            return

        root = tk.Tk()
        root.withdraw()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"track_{ts}.csv",
            initialdir=OUTPUT_DIR,
        )
        root.destroy()
        if path:
            shutil.copy2(src, path)
            print(f"[SAVE] copied → {path}")
    btn_save.on_clicked(_save_as)
    _btn_widgets.append(btn_save)

    # OSC toggle  — pushed right with a gap after LOG controls
    ax_osc = fig.add_axes([0.54, 0.02, 0.10, 0.04])
    btn_osc = Button(ax_osc, "● SEND OSC", color="#444", hovercolor="#444")
    btn_osc.label.set_fontsize(8)
    _osc_btn_state = [False]

    _osc_ip_box = None
    _osc_port_box = None

    def _toggle_osc(event):
        if _osc_btn_state[0]:
            osc_stop()
            btn_osc.label.set_text("● SEND OSC")
            btn_osc.color = "#444"
            btn_osc.hovercolor = "#444"
            _osc_btn_state[0] = False
        else:
            ip = _osc_ip_box.text if _osc_ip_box else CFG["osc_ip"]
            try:
                port = int(_osc_port_box.text) if _osc_port_box else CFG["osc_port"]
            except ValueError:
                port = CFG["osc_port"]
            osc_start(ip, port)
            btn_osc.label.set_text("■ STOP OSC")
            btn_osc.color = "#CC8800"
            btn_osc.hovercolor = "#CC8800"
            _osc_btn_state[0] = True
    btn_osc.on_clicked(_toggle_osc)
    _btn_widgets.append(btn_osc)

    # OSC IP
    ax_ip_lbl = fig.add_axes([0.64, 0.02, 0.03, 0.04])
    ax_ip_lbl.set_facecolor("#1a1a2e"); ax_ip_lbl.axis("off")
    ax_ip_lbl.text(0.5, 0.5, "IP:", color="gray", fontsize=7,
                   ha="center", va="center", transform=ax_ip_lbl.transAxes)

    ax_ip = fig.add_axes([0.67, 0.02, 0.12, 0.04])
    ax_ip.set_facecolor("#2a2a4e")
    _osc_ip_box = TextBox(ax_ip, "", initial=CFG["osc_ip"],
                          color="#2a2a4e", hovercolor="#3a3a5e")
    _osc_ip_box.text_disp.set_color("white")
    _osc_ip_box.text_disp.set_fontsize(8)
    _btn_widgets.append(_osc_ip_box)

    # OSC Port
    ax_port_lbl = fig.add_axes([0.80, 0.02, 0.03, 0.04])
    ax_port_lbl.set_facecolor("#1a1a2e"); ax_port_lbl.axis("off")
    ax_port_lbl.text(0.5, 0.5, "Port:", color="gray", fontsize=7,
                     ha="center", va="center", transform=ax_port_lbl.transAxes)

    ax_port = fig.add_axes([0.83, 0.02, 0.06, 0.04])
    ax_port.set_facecolor("#2a2a4e")
    _osc_port_box = TextBox(ax_port, "", initial=str(CFG["osc_port"]),
                            color="#2a2a4e", hovercolor="#3a3a5e")
    _osc_port_box.text_disp.set_color("white")
    _osc_port_box.text_disp.set_fontsize(8)
    _btn_widgets.append(_osc_port_box)

    # ── Animation update — reads from ring buffers ──
    def _update_display(frame_num):
        if not _display_on[0]:
            return

        elapsed = time.time() - _t_start

        # ── Snapshot prosody frames ──
        with _frame_lock:
            n_want = _display_n[0]
            n_have = len(_frame_buf)
            n_take = min(n_want, n_have)
            if n_take > 0:
                # Efficiently slice last n_take from deque
                frames = list(itertools.islice(
                    _frame_buf, max(0, n_have - n_take), n_have))
            else:
                frames = []

        # ── Snapshot VAD timeline (independent of prosody) ──
        with _vad_buf_lock:
            v_have = len(_vad_buf)
            v_take = min(_display_n[0], v_have)
            if v_take > 0:
                vad_entries = list(itertools.islice(
                    _vad_buf, max(0, v_have - v_take), v_have))
            else:
                vad_entries = []

        # ── Snapshot latest emotion ──
        with _emo_lock:
            emo_entry = _emo_buf[-1].copy() if _emo_buf else None

        # Need at least one timeline (prosody OR VAD) to draw anything.
        if not frames and not vad_entries:
            return

        # Extract arrays from frame dicts
        if frames:
            f_times = np.array([f["time_s"] for f in frames])
        else:
            f_times = np.array([])
        if vad_entries:
            v_times = np.array([v["time_s"] for v in vad_entries])
            v_vals = np.array([v["vad"] for v in vad_entries],
                              dtype=np.float32)
        else:
            v_times = np.array([])
            v_vals = np.array([])

        # Time axis (elapsed seconds): prefer frame extent; fall back to
        # VAD extent; finally to current elapsed window.
        if len(f_times) > 1:
            x_min, x_max = f_times[0], f_times[-1]
        elif len(v_times) > 1:
            x_min, x_max = v_times[0], v_times[-1]
        else:
            x_min = max(0, elapsed - 1.0)
            x_max = elapsed
        if x_max - x_min < 1.0:
            x_min = x_max - 1.0

        for ax in all_ts_axes:
            ax.set_xlim(x_min, x_max)

        # ── Waveform — raw audio matching the frame time span ──
        display_sec = x_max - x_min + 0.5
        audio = _get_recent_audio(display_sec)
        if audio is not None and len(audio) > 0:
            n_samples = len(audio)
            wave_t0 = elapsed - n_samples / SR
            wave_times = np.linspace(wave_t0, elapsed, n_samples)
            line_wave.set_data(wave_times, audio)
        else:
            line_wave.set_data([], [])

        # ── Emotion bars (with optional decay / zero-on-silence) ──
        if _has_emo_display and emo_bars is not None and emo_entry is not None:
            scores = emo_entry.get("scores", {})
            vals = [scores.get(d, 0.0) for d in EMOTION_DIMS]

            # Zero bars immediately when VAD gate is closed
            if CFG.get("emo_zero_on_silence", False):
                recent_voiced = _vad_voiced_fraction(0.2)
                if recent_voiced is not None and recent_voiced == 0.0:
                    vals = [0.0] * len(EMOTION_DIMS)

            # Exponential decay based on age of last prediction
            if CFG.get("emo_decay_active", False):
                tau = CFG.get("emo_decay_tau", 2.0)
                age = elapsed - emo_entry.get("time_s", elapsed)
                if age > 0 and tau > 0:
                    decay = np.exp(-age / tau)
                    vals = [v * decay for v in vals]

            top_idx = int(np.argmax(vals))
            for j, dim in enumerate(EMOTION_DIMS):
                emo_bars[j].set_height(vals[j])
                if j == top_idx and vals[j] > 0.01:
                    emo_bars[j].set_alpha(1.0)
                    emo_bars[j].set_edgecolor("white")
                    emo_bars[j].set_linewidth(1.5)
                else:
                    emo_bars[j].set_alpha(0.6)
                    emo_bars[j].set_edgecolor("none")
                    emo_bars[j].set_linewidth(0)
        elif _has_emo_display and emo_bars is not None:
            for j in range(len(EMOTION_DIMS)):
                emo_bars[j].set_height(0)

        # ── VAD strip (driven by its own timeline buffer) ──
        if _proc_vad[0] and len(v_times) > 2:
            binary_vad = np.clip(v_vals, 0, 1).astype(float)
            xs = np.repeat(v_times, 2)
            ys = np.repeat(binary_vad, 2)
            xs = np.concatenate([[v_times[0]], xs[1:-1], [v_times[-1]]])
            ys = np.concatenate([[0], ys[:-2], [0]])
            _vad_poly.set_xy(np.column_stack([xs, ys]))
            _vad_poly.set_visible(True)
        else:
            _vad_poly.set_visible(False)

        # ── Feature strips ──
        if _proc_prosody[0] and len(f_times) > 0:
            for i, (key, label, color, is_nz, ylim) in enumerate(FEATURES):
                vals = np.array([f.get(key, float("nan")) for f in frames],
                                dtype=np.float32)
                lines[i].set_data(f_times, vals)
        else:
            for ln in lines:
                ln.set_data([], [])

        # ── Title bar (always visible: compute rates) ──
        prs_hz = _opensmile_rate[0]
        emo_hz = _emo_rate[0]
        avg_hz = _logger_rate[0]
        fig.suptitle(
            f"PRS: {prs_hz:.0f}/sec  │  EMO: {emo_hz:.1f}/sec  │  "
            f"AVG DATA: {avg_hz:.1f}/sec",
            fontsize=9, color="white",
        )

        # ── Recording timer (near LOG buttons) ──
        if _log_on:
            rec_secs = _rec_elapsed[0] + (time.time() - _rec_resume_t[0])
        else:
            rec_secs = _rec_elapsed[0]
        rh, rrem = divmod(int(rec_secs), 3600)
        rm, rs = divmod(rrem, 60)
        if _log_on:
            _rec_timer_text.set_text(f"⏺ {rh:02d}:{rm:02d}:{rs:02d}")
            _rec_timer_text.set_color("#FF4444")
        elif rec_secs > 0:
            _rec_timer_text.set_text(f"⏸ {rh:02d}:{rm:02d}:{rs:02d}")
            _rec_timer_text.set_color("#888888")
        else:
            _rec_timer_text.set_text("")
            _rec_timer_text.set_color("#888888")


# ═══════════════════════════════════════════════════════════════════
# 14. HEADLESS LOOP
# ═══════════════════════════════════════════════════════════════════
def _headless_loop():
    """No display — threads handle everything. Just wait."""
    print("Running headless. Press Ctrl-C to stop.")
    while not _stop_event.is_set():
        time.sleep(1.0)
        elapsed = time.time() - _t_start
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        with _frame_lock:
            n_frames = len(_frame_buf)
        with _emo_lock:
            n_emo = len(_emo_buf)
        avg_hz = _logger_rate[0]
        if _log_on:
            rec_secs = _rec_elapsed[0] + (time.time() - _rec_resume_t[0])
        else:
            rec_secs = _rec_elapsed[0]
        rh2, rrem2 = divmod(int(rec_secs), 3600)
        rm2, rs2 = divmod(rrem2, 60)
        rec_icon = "⏺" if _log_on else "⏸" if rec_secs > 0 else " "
        print(f"[{h:02d}:{m:02d}:{s:02d}] "
              f"PRS={_opensmile_rate[0]:.0f}/s  "
              f"EMO={_emo_rate[0]:.1f}/s  "
              f"AVG={avg_hz:.1f}/s  "
              f"{rec_icon}{rh2:02d}:{rm2:02d}:{rs2:02d}",
              end="\r")


# ═══════════════════════════════════════════════════════════════════
# 15. CLEANUP + MAIN
# ═══════════════════════════════════════════════════════════════════
def _cleanup():
    global _stream
    _stop_event.set()
    log_stop()
    if _osc_on:
        osc_stop()
    if _stream is not None:
        try:
            _stream.abort()
            _stream.close()
        except Exception:
            pass
        _stream = None
    print("\nClean exit.")


signal.signal(signal.SIGINT, lambda *_: (_cleanup(), sys.exit(0)))
signal.signal(signal.SIGTERM, lambda *_: (_cleanup(), sys.exit(0)))


def main():
    global _stream, _t_start

    print(f"Config: SR={SR}, display={'ON' if _display_enabled else 'OFF'}, "
            f"emotion_model={CFG['emotion_model']} "
            f"(loaded: {'YES' if _emotion_loaded[0] else 'NO'}, initial: "
            f"{'ON' if _proc_emotion[0] else 'OFF'})")
    print(f"  openSMILE: interval={_opensmile_interval[0]}s, margin={_OPENSMILE_MARGIN}s")
    print(f"  Emotion:   window={_emo_window[0]}s, hop={_emo_hop[0]}s")
    print(f"  Logger:    interval={_log_interval[0]}s")
    print(f"  Display:   N={_display_n[0]} frames ({_display_n[0]*0.01:.1f}s)")
    # Show available input devices and which one we'll use
    list_input_devices()
    dev_idx = resolve_device(CFG.get("audio_device"))
    if dev_idx is None:
        print("[AUDIO] using system default input device")
    else:
        try:
            dev_name = sd.query_devices(dev_idx)["name"]
            print(f"[AUDIO] using device [{dev_idx}] {dev_name}")
        except Exception:
            print(f"[AUDIO] using device index {dev_idx}")

    print("Starting… speak into the mic!")

    # Determine native sample rate of the chosen input device. Linux/ALSA
    # refuses to open a stream at any rate the device doesn't natively
    # support (no transparent resampling like CoreAudio on macOS). If the
    # device's native rate differs from SR, open at native rate and let
    # _audio_callback() resample on the fly. --no-resample disables this
    # and forces the stream to open at SR (legacy behaviour).
    global _INPUT_SR
    if ARGS.no_resample:
        native_sr = SR
        print("[AUDIO] --no-resample: forcing stream open at SR "
              f"({SR} Hz), no native-rate detection")
    else:
        try:
            if dev_idx is not None:
                native_sr = int(sd.query_devices(dev_idx)["default_samplerate"])
            else:
                native_sr = int(sd.query_devices(kind="input")["default_samplerate"])
        except Exception:
            native_sr = SR
    _INPUT_SR = native_sr
    if native_sr != SR:
        print(f"[AUDIO] device native rate={native_sr} Hz, target SR={SR} Hz "
              f"— will resample in callback")

    try:
        _stream = sd.InputStream(
            samplerate=native_sr, channels=1, dtype="float32",
            blocksize=int(native_sr * 0.05), callback=_audio_callback,
            device=dev_idx,
        )
        _stream.start()
        _t_start = time.time()

        # Start processing threads
        threading.Thread(target=_opensmile_thread, daemon=True,
                         name="opensmile").start()

        threading.Thread(target=_vad_thread, daemon=True,
                         name="vad").start()

        if _emo_model is not None:
            threading.Thread(target=_emotion_thread, daemon=True,
                             name="emotion").start()

        threading.Thread(target=_logger_thread, daemon=True,
                         name="logger").start()

        # Start remote control listener
        threading.Thread(target=_start_ctrl_listener, daemon=True).start()

        # Discovery heartbeat (UDP broadcast /hello so bridge learns Pi IP)
        threading.Thread(target=_hello_thread, daemon=True,
                         name="hello").start()

        # Per-address OSC send-rate stats (stdout + /stats/rate to browser)
        threading.Thread(target=_rate_stats_thread, daemon=True,
                         name="rate_stats").start()

        # Self-telemetry: RSS / CPU% / temperature (stdout + /stats/self)
        threading.Thread(target=_self_stats_thread, daemon=True,
                         name="self_stats").start()

        # Auto-start log/OSC from config or CLI
        if CFG.get("log_active", False):
            log_start()
        if ARGS.osc_autostart or CFG.get("osc_active", False):
            osc_start()

        if not _display_enabled:
            _headless_loop()
        else:
            anim = FuncAnimation(
                fig, _update_display,
                interval=_display_refresh_ms, blit=False,
                cache_frame_data=False,
            )
            plt.show()
    finally:
        _cleanup()

    print("Done.")
    os._exit(0)


if __name__ == "__main__":
    main()
