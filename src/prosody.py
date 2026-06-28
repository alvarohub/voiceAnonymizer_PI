"""
Prosody / acoustic feature extraction.

Two tiers of features:

1. **eGeMAPSv02** (via openSMILE) — 88 standardised utterance-level
   functionals: F0, jitter, shimmer, loudness, HNR, formants F1-F3,
   MFCCs 1-4, spectral slopes, alpha ratio, etc.  This is the de-facto
   standard feature set for affective computing research (Eyben et al.,
   2016).  All 88 go to CSV; a small subset is shown in the live GUI.

2. **pyworld fallback** — basic F0 + energy + ZCR when openSMILE is
   not installed.

Neither representation is reversible to speech → safe for anonymous
recording.
"""

from __future__ import annotations

import threading
import numpy as np

# ---------- try openSMILE first, then pyworld ----------
# openSMILE's C library (libSMILEapi) may use process-global state
# (pitch candidate buffers, voicing probability history) that gets
# corrupted when two Smile instances call process_signal concurrently,
# even from different Python objects.  Fix: each thread gets its own
# Smile instances via thread-local storage.

_thread_local = threading.local()

try:
    import opensmile as _opensmile
    # Create module-level instances only for feature name discovery
    _smile_func_init = _opensmile.Smile(
        feature_set=_opensmile.FeatureSet.eGeMAPSv02,
        feature_level=_opensmile.FeatureLevel.Functionals,
    )
    HAS_OPENSMILE = True
except Exception:
    HAS_OPENSMILE = False
    _smile_func_init = None


def _get_smile_func():
    """Get/create a thread-local Functionals Smile instance."""
    if not hasattr(_thread_local, "smile_func"):
        _thread_local.smile_func = _opensmile.Smile(
            feature_set=_opensmile.FeatureSet.eGeMAPSv02,
            feature_level=_opensmile.FeatureLevel.Functionals,
        )
    return _thread_local.smile_func


def _get_smile_lld():
    """Get/create a thread-local LLD Smile instance."""
    if not hasattr(_thread_local, "smile_lld"):
        _thread_local.smile_lld = _opensmile.Smile(
            feature_set=_opensmile.FeatureSet.eGeMAPSv02,
            feature_level=_opensmile.FeatureLevel.LowLevelDescriptors,
        )
    return _thread_local.smile_lld

try:
    import pyworld as pw
    HAS_PYWORLD = True
except ImportError:
    HAS_PYWORLD = False


# ── Feature names exported for CSV / display ────────────────────────

# The 88 eGeMAPSv02 column names (populated at import time if available)
OPENSMILE_FEATURES: list[str] = []
if HAS_OPENSMILE:
    OPENSMILE_FEATURES = list(_smile_func_init.feature_names)

# Subset shown in the live GUI timeline
DISPLAY_KEYS = [
    "F0semitoneFrom27.5Hz_sma3nz_amean",   # pitch (why these values? see test/test_prosody.py)
    "loudness_sma3_amean",                   # perceived loudness
    "jitterLocal_sma3nz_amean",              # voice quality
    "shimmerLocaldB_sma3nz_amean",           # voice quality
    "HNRdBACF_sma3nz_amean",                # harmonics-to-noise
]

# Short labels for the GUI strips (same order as DISPLAY_KEYS)
DISPLAY_LABELS = ["F0", "LOUD", "JITTER", "SHIMMER", "HNR"]

DISPLAY_COLORS = ["#00DDFF", "#44FF44", "#FF66AA", "#FFAA33", "#AA88FF"]

# ylim ranges for display strips — calibrated via test/test_prosody.py
# on synthetic + real speech signals.
# F0: 15 st ≈ 65 Hz, 50 st ≈ 501 Hz — covers human voice (bass → child)
DISPLAY_YLIMS = [
    (15, 50),    # F0 semitones from 27.5 Hz (speech range ~19-47 st)
    (0, 2.5),    # loudness (sone-like, speech 0.05-2.0 typical)
    (0, 0.015),  # jitter (fraction, speech 0.003-0.01)
    (0, 1.0),    # shimmer dB (speech 0.2-0.5, noisy up to 0.8)
    (0, 30),     # HNR dB (speech 5-15 typical)
]

# Human-readable units for display annotation (same order as DISPLAY_KEYS)
DISPLAY_UNITS = ["st (65–500 Hz)", "sone", "frac", "dB", "dB"]

# Keys that use _sma3nz (non-zero = voiced-only); 0 means "unvoiced", not a real value.
# These zeros must be replaced with NaN before display so gaps show during silence.
NZ_KEYS = {
    "F0semitoneFrom27.5Hz_sma3nz",
    "jitterLocal_sma3nz",
    "shimmerLocaldB_sma3nz",
    "HNRdBACF_sma3nz",
    # functionals equivalents (for completeness)
    "F0semitoneFrom27.5Hz_sma3nz_amean",
    "jitterLocal_sma3nz_amean",
    "shimmerLocaldB_sma3nz_amean",
    "HNRdBACF_sma3nz_amean",
}

# ── LLD (frame-level) display config ──
# openSMILE LowLevelDescriptor column names (20ms windows, 10ms hop)
LLD_DISPLAY_KEYS = [
    "F0semitoneFrom27.5Hz_sma3nz",
    "Loudness_sma3",
    "jitterLocal_sma3nz",
    "shimmerLocaldB_sma3nz",
    "HNRdBACF_sma3nz",
]
LLD_DISPLAY_LABELS = DISPLAY_LABELS
LLD_DISPLAY_COLORS = DISPLAY_COLORS
LLD_DISPLAY_YLIMS = DISPLAY_YLIMS
LLD_DISPLAY_UNITS = DISPLAY_UNITS

# Legacy basic features (pyworld fallback)
BASIC_FEATURES = [
    "f0_mean", "f0_std", "f0_min", "f0_max",
    "energy_rms", "energy_db", "zcr", "voiced_ratio",
]
if HAS_PYWORLD:
    BASIC_FEATURES.append("aperiodicity_mean")

# Canonical list used by track_writer
PROSODY_FEATURES = OPENSMILE_FEATURES if HAS_OPENSMILE else BASIC_FEATURES


# ── Short-gap interpolation for sma3nz features ───────────────────
# openSMILE's voicing detector can classify individual frames as
# unvoiced even during sustained phonation (noisy frame-level decision).
# For sma3nz features, that means F0/jitter/shimmer/HNR flicker to 0
# on isolated frames, creating visual fragmentation.  We bridge gaps
# of ≤ MAX_NZ_GAP frames by linear interpolation after extraction.
_MAX_NZ_GAP = 5  # frames × 10ms hop = 50ms max gap to bridge

# LLD-level nz keys (without _amean suffix)
_NZ_LLD_KEYS = {
    "F0semitoneFrom27.5Hz_sma3nz",
    "jitterLocal_sma3nz",
    "shimmerLocaldB_sma3nz",
    "HNRdBACF_sma3nz",
}


def _bridge_nz_gaps(frames: dict[str, np.ndarray], max_gap: int = _MAX_NZ_GAP) -> dict[str, np.ndarray]:
    """For sma3nz features, replace 0→NaN then interpolate gaps ≤ max_gap frames.

    Short gaps get linear interpolation.  Long gaps (real silence) stay NaN.
    This is done *before* the display ingest so the rendering sees continuous
    contours during voiced speech.
    """
    for col in list(frames.keys()):
        if col not in _NZ_LLD_KEYS:
            continue
        vals = frames[col].copy()
        # Mark unvoiced frames
        vals[vals <= 0] = np.nan
        nans = np.isnan(vals)
        if not np.any(nans) or np.all(nans):
            frames[col] = vals
            continue

        # Find contiguous NaN runs
        d = np.diff(nans.astype(np.int8))
        starts = np.where(d == 1)[0] + 1
        ends = np.where(d == -1)[0] + 1
        if nans[0]:
            starts = np.concatenate([[0], starts])
        if nans[-1]:
            ends = np.concatenate([ends, [len(vals)]])

        for s, e in zip(starts, ends):
            gap_len = e - s
            if gap_len > max_gap:
                continue  # real silence — leave as NaN
            left = vals[s - 1] if s > 0 else np.nan
            right = vals[e] if e < len(vals) else np.nan
            if np.isnan(left) and np.isnan(right):
                continue
            elif np.isnan(left):
                vals[s:e] = right
            elif np.isnan(right):
                vals[s:e] = left
            else:
                vals[s:e] = np.linspace(left, right, gap_len + 2)[1:-1]
        frames[col] = vals
    return frames


# ── Audio normalization for openSMILE ──────────────────────────────
# openSMILE's SHS pitch tracker needs sufficient signal level to detect
# voicing.  Built-in laptop mics often produce very quiet audio
# (peak < 0.1), causing the voicing probability to stay below threshold
# and returning F0 = 0 even on clear speech.  We peak-normalize to a
# target level before processing.  Note: this DOES shift loudness_sma3
# proportionally, but with the sliding-window approach (3s buffers)
# the peak is stable enough that loudness stays consistent across cycles.
_OPENSMILE_TARGET_PEAK = 0.5   # normalize audio peak to this level
_OPENSMILE_MIN_PEAK = 0.01     # below this, don't amplify (pure noise)


def _normalize_for_opensmile(audio: np.ndarray) -> np.ndarray:
    """Peak-normalize audio to _OPENSMILE_TARGET_PEAK.

    Returns a copy; never modifies the input.
    Loudness features are computed on a perceptual scale and will
    shift proportionally — this is acceptable for live display.
    """
    peak = np.max(np.abs(audio))
    if peak < _OPENSMILE_MIN_PEAK:
        return audio  # pure silence / noise floor — don't amplify
    gain = _OPENSMILE_TARGET_PEAK / peak
    return (audio * gain).astype(np.float32)


# ── Extraction functions ────────────────────────────────────────────

def extract_prosody(audio: np.ndarray, sr: int = 16000) -> dict[str, float]:
    """
    Extract utterance-level prosody features from a mono audio chunk.

    If openSMILE is available, returns all 88 eGeMAPSv02 functionals.
    Otherwise falls back to pyworld-based basics.
    """
    if HAS_OPENSMILE:
        return _extract_opensmile(audio, sr)
    return _extract_basic(audio, sr)


def extract_prosody_lld(audio: np.ndarray, sr: int = 16000) -> dict | None:
    """Extract frame-level (20ms window, 10ms hop) LLD features via openSMILE.

    Returns dict with:
        'times'  — np.array of frame midpoint times (seconds from chunk start)
        'frames' — dict of {column_name: np.array} for all 25 LLD features
    Returns None if openSMILE is unavailable or audio is too quiet.
    """
    if not HAS_OPENSMILE:
        return None
    audio = _normalize_for_opensmile(audio)
    smile = _get_smile_lld()
    df = smile.process_signal(audio, sampling_rate=sr)
    starts = np.array([t.total_seconds() for t in df.index.get_level_values("start")])
    ends = np.array([t.total_seconds() for t in df.index.get_level_values("end")])
    times = (starts + ends) / 2.0
    frames = {col: df[col].values.astype(np.float32) for col in df.columns}
    return {"times": times, "frames": frames}


# Diagnostic: save one WAV chunk for offline F0 verification
_diag_wav_saved = False

def _maybe_save_diag_wav(audio: np.ndarray, sr: int, orig_peak: float) -> None:
    """Save the first chunk with audible audio to output/diag_lld_chunk.wav."""
    global _diag_wav_saved
    if _diag_wav_saved or orig_peak < 0.02:
        return
    _diag_wav_saved = True
    import os, wave, struct
    path = os.path.join("output", "diag_lld_chunk.wav")
    os.makedirs("output", exist_ok=True)
    data = (audio * 32767).clip(-32768, 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())
    print(f"\n[DIAG] Saved {path} ({len(audio)/sr:.2f}s) for offline F0 check")


def _extract_opensmile(audio: np.ndarray, sr: int) -> dict[str, float]:
    """88 eGeMAPSv02 functionals via openSMILE."""
    audio = _normalize_for_opensmile(audio)
    smile = _get_smile_func()
    df = smile.process_signal(audio, sampling_rate=sr)
    # DataFrame has 1 row; convert to dict
    return {col: float(df.iloc[0][col]) for col in df.columns}


def _extract_basic(audio: np.ndarray, sr: int) -> dict[str, float]:
    """Fallback: F0, energy, ZCR via pyworld / numpy."""
    features: dict[str, float] = {}

    # RMS energy
    rms = float(np.sqrt(np.mean(audio ** 2)))
    features["energy_rms"] = rms
    features["energy_db"] = float(20 * np.log10(rms + 1e-10))

    # Zero-crossing rate
    features["zcr"] = float(np.mean(np.abs(np.diff(np.sign(audio))) > 0))

    # F0 / pitch
    if HAS_PYWORLD:
        audio_f64 = audio.astype(np.float64)
        f0, _timeaxis = pw.harvest(audio_f64, sr, frame_period=10.0)
        sp = pw.cheaptrick(audio_f64, f0, _timeaxis, sr)
        ap = pw.d4c(audio_f64, f0, _timeaxis, sr)

        voiced = f0 > 0
        features["voiced_ratio"] = float(np.mean(voiced))
        if voiced.any():
            f0v = f0[voiced]
            features["f0_mean"] = float(np.mean(f0v))
            features["f0_std"] = float(np.std(f0v))
            features["f0_min"] = float(np.min(f0v))
            features["f0_max"] = float(np.max(f0v))
        else:
            features.update({"f0_mean": 0.0, "f0_std": 0.0, "f0_min": 0.0, "f0_max": 0.0})
        features["aperiodicity_mean"] = float(np.mean(ap))
    else:
        features.update({
            "voiced_ratio": 0.0, "f0_mean": 0.0, "f0_std": 0.0,
            "f0_min": 0.0, "f0_max": 0.0,
        })

    return features
