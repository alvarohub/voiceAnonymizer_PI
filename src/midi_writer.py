"""
Convert speech analysis CSV tracks to MIDI files.

═══════════════════════════════════════════════════════════════════
MIDI MAPPING — Speech Prosody & Emotion → MIDI
═══════════════════════════════════════════════════════════════════

NOTE EVENTS (gated by VAD):
  F0 semitones from 27.5Hz → MIDI note  (round(st) + 21)
  Loudness                 → Velocity   (0–2.5 → 1–127)
  VAD 0→1                  → note_on    (speech onset = attack)
  VAD 1→0                  → note_off   (speech offset = release)
  F0 jump > threshold      → retrigger  (new note mid-speech)

PITCH BEND:
  F0 fractional semitone   → pitch bend (±2 st range, sub-semitone detail)

PROSODY → CC (continuous, every tick):
  Loudness                 → CC  2  Breath Controller
  Jitter                   → CC  1  Modulation (vocal instability)
  Shimmer                  → CC 74  Brightness / Filter Cutoff
  HNR                      → CC 71  Resonance / Timbre

EMOTIONS → CC:
  emo_confidence           → CC 11  Expression
  angry                    → CC 73  Attack Time (anger → sharp)
  sad                      → CC 72  Release Time (sadness → long)
  happy                    → CC 91  Reverb Depth (joy → spacious)
  fearful                  → CC 76  Vibrato Rate (fear → trembling)
  surprised                → CC 77  Vibrato Depth (surprise → wide)
  disgusted                → CC 92  Tremolo Depth
  other                    → CC 93  Chorus Depth
  unknown                  → CC 95  Phaser Depth

METADATA:
  VAD state                → CC 64  Sustain Pedal (127=speaking, 0=silence)
  Top emotion label        → MIDI text event (marker per change)

═══════════════════════════════════════════════════════════════════

Usage:
    # As module
    from src.midi_writer import csv_to_midi
    csv_to_midi("output/track_20260417_014225.csv", "output/track_20260417_014225.mid")

    # As script
    python -m src.midi_writer output/track_20260417_014225.csv
    python -m src.midi_writer output/track_20260417_014225.csv -o my_output.mid
    python -m src.midi_writer output/track_20260417_014225.csv --channel 2 --bend-range 4
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import mido


# ─── MIDI Mapping Constants ──────────────────────────────────────

# 27.5 Hz = A0 = MIDI note 21.  F0 in semitones from 27.5 Hz →
# MIDI note = round(F0_st) + F0_MIDI_OFFSET
F0_MIDI_OFFSET = 21

# Pitch bend range in semitones (standard GM default)
PITCH_BEND_RANGE = 2

# If F0 jumps more than this many semitones between ticks,
# retrigger a new note (preserves melodic contour)
RETRIGGER_THRESHOLD_ST = 2.0

# ─── Value ranges for scaling to 0-127 ───────────────────────────
# (from the FEATURES config and DISPLAY_YLIMS in main.py / prosody.py)
LOUDNESS_MAX = 2.5
JITTER_MAX = 0.05      # typical speech jitter (local) upper bound
SHIMMER_MAX = 2.0       # shimmer dB typical upper bound
HNR_MAX = 30.0          # HNR dB upper bound

# ─── CC numbers ───────────────────────────────────────────────────
CC_MODULATION     = 1    # Jitter → modulation
CC_BREATH         = 2    # Loudness → breath controller
CC_EXPRESSION     = 11   # Emotion confidence → expression
CC_SUSTAIN        = 64   # VAD state → sustain pedal
CC_RESONANCE      = 71   # HNR → resonance / timbre
CC_RELEASE_TIME   = 72   # sad → release
CC_ATTACK_TIME    = 73   # angry → attack
CC_BRIGHTNESS     = 74   # Shimmer → brightness / filter cutoff
CC_VIBRATO_RATE   = 76   # fearful → vibrato rate
CC_VIBRATO_DEPTH  = 77   # surprised → vibrato depth
CC_REVERB         = 91   # happy → reverb
CC_TREMOLO        = 92   # disgusted → tremolo
CC_CHORUS         = 93   # other → chorus
CC_PHASER         = 95   # unknown → phaser

# Emotion dimension → CC mapping (order matches EMOTION_DIMS in main.py)
EMOTION_CC_MAP = {
    "angry":     CC_ATTACK_TIME,
    "sad":       CC_RELEASE_TIME,
    "happy":     CC_REVERB,
    "fearful":   CC_VIBRATO_RATE,
    "surprised": CC_VIBRATO_DEPTH,
    "disgusted": CC_TREMOLO,
    "other":     CC_CHORUS,
    "unknown":   CC_PHASER,
    # "neutral" has no dedicated CC — it's the default / absence of others
}

# Prosody feature → (CC number, max value for scaling)
PROSODY_CC_MAP = {
    "Loudness_sma3":               (CC_BREATH,     LOUDNESS_MAX),
    "jitterLocal_sma3nz":          (CC_MODULATION,  JITTER_MAX),
    "shimmerLocaldB_sma3nz":       (CC_BRIGHTNESS,  SHIMMER_MAX),
    "HNRdBACF_sma3nz":            (CC_RESONANCE,   HNR_MAX),
}

# MIDI timing
DEFAULT_TEMPO_BPM = 120
DEFAULT_TICKS_PER_BEAT = 480


# ─── Helpers ──────────────────────────────────────────────────────

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _scale_to_midi(value: float, min_val: float, max_val: float,
                   midi_lo: int = 0, midi_hi: int = 127) -> int:
    """Linearly scale a float range to MIDI 0-127 (or sub-range)."""
    if max_val <= min_val:
        return midi_lo
    frac = (value - min_val) / (max_val - min_val)
    frac = _clamp(frac, 0.0, 1.0)
    return int(round(midi_lo + frac * (midi_hi - midi_lo)))


def _f0_to_note_and_bend(f0_st: float, bend_range: int = PITCH_BEND_RANGE
                          ) -> tuple[int, int]:
    """Convert F0 in semitones from 27.5 Hz to (MIDI note, pitch bend).

    Returns:
        (note, bend) where note is 0-127 and bend is -8192..+8191
    """
    note = int(round(f0_st)) + F0_MIDI_OFFSET
    note = _clamp(note, 0, 127)
    # Fractional semitone → pitch bend
    frac = f0_st - round(f0_st)  # -0.5 .. +0.5
    bend = int(round(frac * 8192 / bend_range))
    bend = int(_clamp(bend, -8192, 8191))
    return int(note), bend


def _ms_to_ticks(delta_ms: float, ticks_per_beat: int, tempo_us: int) -> int:
    """Convert a delta in milliseconds to MIDI ticks."""
    if delta_ms <= 0:
        return 0
    ms_per_beat = tempo_us / 1000.0
    return max(0, int(round(delta_ms * ticks_per_beat / ms_per_beat)))


def _safe_float(val: str, default: float = 0.0) -> float:
    """Parse a CSV cell to float, returning default for empty/invalid."""
    if not val or val.strip() == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ─── CSV Row Parser ───────────────────────────────────────────────

@dataclass
class TickData:
    time_ms: float
    vad: bool
    f0_st: float           # F0 in semitones from 27.5 Hz (NaN if unvoiced)
    loudness: float
    jitter: float
    shimmer: float
    hnr: float
    emo_label: str
    emo_confidence: float
    emo_scores: dict[str, float] = field(default_factory=dict)


def _parse_csv(csv_path: str) -> list[TickData]:
    """Read a speech analysis CSV into a list of TickData."""
    ticks = []
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            f0_raw = _safe_float(row.get("F0semitoneFrom27.5Hz_sma3nz", ""), float("nan"))
            emo_scores = {}
            for dim in ("angry", "disgusted", "fearful", "happy", "neutral",
                        "other", "sad", "surprised", "unknown"):
                emo_scores[dim] = _safe_float(row.get(dim, ""), 0.0)

            ticks.append(TickData(
                time_ms=_safe_float(row.get("time_ms", "0")),
                vad=int(_safe_float(row.get("vad", "0"))) == 1,
                f0_st=f0_raw,
                loudness=_safe_float(row.get("Loudness_sma3", "")),
                jitter=_safe_float(row.get("jitterLocal_sma3nz", "")),
                shimmer=_safe_float(row.get("shimmerLocaldB_sma3nz", "")),
                hnr=_safe_float(row.get("HNRdBACF_sma3nz", "")),
                emo_label=row.get("emo_label", "").strip(),
                emo_confidence=_safe_float(row.get("emo_confidence", ""), 0.0),
                emo_scores=emo_scores,
            ))
    return ticks


# ─── MIDI Generation ─────────────────────────────────────────────

def csv_to_midi(
    csv_path: str,
    midi_path: Optional[str] = None,
    channel: int = 0,
    tempo_bpm: int = DEFAULT_TEMPO_BPM,
    ticks_per_beat: int = DEFAULT_TICKS_PER_BEAT,
    bend_range: int = PITCH_BEND_RANGE,
) -> str:
    """Convert a speech analysis CSV track to a MIDI file.

    Args:
        csv_path:       Path to input CSV (from main.py track logging)
        midi_path:      Output .mid path (default: same name as CSV)
        channel:        MIDI channel 0-15
        tempo_bpm:      Tempo in BPM (affects timing resolution)
        ticks_per_beat: MIDI ticks per quarter note
        bend_range:     Pitch bend range in semitones

    Returns:
        Path to the written MIDI file.
    """
    if midi_path is None:
        midi_path = os.path.splitext(csv_path)[0] + ".mid"

    ticks = _parse_csv(csv_path)
    if not ticks:
        raise ValueError(f"No data rows in {csv_path}")

    tempo_us = int(60_000_000 / tempo_bpm)  # microseconds per beat

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    mid.tracks.append(track)

    # Header: tempo + track name
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    track.append(mido.MetaMessage("track_name",
                                  name="Speech Prosody + Emotion", time=0))

    # State tracking
    current_note: Optional[int] = None   # currently sounding MIDI note
    prev_time_ms = ticks[0].time_ms
    last_emo_label = ""
    last_cc_values: dict[int, int] = {}  # CC number → last sent value

    def _send_cc(cc: int, value: int, delta: int = 0):
        """Send CC only if value changed (reduce MIDI clutter)."""
        value = int(_clamp(value, 0, 127))
        if last_cc_values.get(cc) != value:
            track.append(mido.Message("control_change",
                                      channel=channel, control=cc,
                                      value=value, time=delta))
            last_cc_values[cc] = value
            return True
        return False

    for tick in ticks:
        delta_ms = tick.time_ms - prev_time_ms
        delta_ticks = _ms_to_ticks(delta_ms, ticks_per_beat, tempo_us)
        prev_time_ms = tick.time_ms

        # We accumulate delta_ticks and assign to the first message of
        # this tick, then set subsequent messages in the same tick to 0.
        remaining_delta = delta_ticks

        def _consume_delta() -> int:
            nonlocal remaining_delta
            d = remaining_delta
            remaining_delta = 0
            return d

        # ── VAD → Sustain pedal ──
        vad_cc = 127 if tick.vad else 0
        if _send_cc(CC_SUSTAIN, vad_cc, _consume_delta()):
            pass

        # ── Prosody CCs (always sent, even during silence) ──
        for feat_key, (cc_num, max_val) in PROSODY_CC_MAP.items():
            raw = getattr(tick, {
                "Loudness_sma3": "loudness",
                "jitterLocal_sma3nz": "jitter",
                "shimmerLocaldB_sma3nz": "shimmer",
                "HNRdBACF_sma3nz": "hnr",
            }[feat_key])
            if math.isnan(raw):
                continue  # don't send CC for NaN (unvoiced nz features)
            val = _scale_to_midi(raw, 0.0, max_val)
            _send_cc(cc_num, val, _consume_delta())

        # ── Emotion CCs ──
        if tick.emo_confidence > 0:
            _send_cc(CC_EXPRESSION,
                     _scale_to_midi(tick.emo_confidence, 0.0, 1.0),
                     _consume_delta())

        for dim, cc_num in EMOTION_CC_MAP.items():
            score = tick.emo_scores.get(dim, 0.0)
            _send_cc(cc_num, _scale_to_midi(score, 0.0, 1.0),
                     _consume_delta())

        # ── Emotion label → text marker (on change) ──
        if tick.emo_label and tick.emo_label != last_emo_label:
            track.append(mido.MetaMessage(
                "marker",
                text=f"{tick.emo_label} ({tick.emo_confidence:.0%})",
                time=_consume_delta()))
            last_emo_label = tick.emo_label

        # ── Note events (gated by VAD + F0) ──
        has_pitch = not math.isnan(tick.f0_st) and tick.f0_st > 0
        want_note = tick.vad and has_pitch

        if want_note:
            note, bend = _f0_to_note_and_bend(tick.f0_st, bend_range)
            velocity = _scale_to_midi(tick.loudness, 0.0, LOUDNESS_MAX,
                                      midi_lo=1, midi_hi=127)

            # Should we retrigger? (pitch jump or no current note)
            should_start = (
                current_note is None
                or abs(note - current_note) >= RETRIGGER_THRESHOLD_ST
            )

            if should_start:
                # End previous note
                if current_note is not None:
                    track.append(mido.Message(
                        "note_off", channel=channel,
                        note=current_note, velocity=0,
                        time=_consume_delta()))

                # Pitch bend before note_on
                track.append(mido.Message(
                    "pitchwheel", channel=channel,
                    pitch=bend, time=_consume_delta()))

                # Start new note
                track.append(mido.Message(
                    "note_on", channel=channel,
                    note=note, velocity=velocity,
                    time=_consume_delta()))
                current_note = note
            else:
                # Same note continues — update pitch bend for intonation
                track.append(mido.Message(
                    "pitchwheel", channel=channel,
                    pitch=bend, time=_consume_delta()))

        elif current_note is not None:
            # VAD off or no pitch → note off
            track.append(mido.Message(
                "note_off", channel=channel,
                note=current_note, velocity=0,
                time=_consume_delta()))
            current_note = None

    # ── End: close any hanging note ──
    if current_note is not None:
        track.append(mido.Message(
            "note_off", channel=channel,
            note=current_note, velocity=0, time=0))

    # End of track
    track.append(mido.MetaMessage("end_of_track", time=0))

    os.makedirs(os.path.dirname(midi_path) or ".", exist_ok=True)
    mid.save(midi_path)

    # Summary
    n_notes = sum(1 for msg in track if msg.type == "note_on")
    n_cc = sum(1 for msg in track if msg.type == "control_change")
    n_markers = sum(1 for msg in track if hasattr(msg, 'type')
                    and isinstance(msg, mido.MetaMessage)
                    and msg.type == "marker")
    duration_s = (ticks[-1].time_ms - ticks[0].time_ms) / 1000.0

    print(f"[MIDI] {midi_path}")
    print(f"       {len(ticks)} ticks, {duration_s:.1f}s → "
          f"{n_notes} notes, {n_cc} CCs, {n_markers} markers")

    return midi_path


# ─── CLI ──────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Convert speech analysis CSV to MIDI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("csv_path", help="Input CSV track file")
    p.add_argument("-o", "--output", default=None,
                   help="Output .mid path (default: same name as CSV)")
    p.add_argument("--channel", type=int, default=0,
                   help="MIDI channel 0-15 (default: 0)")
    p.add_argument("--tempo", type=int, default=DEFAULT_TEMPO_BPM,
                   help="Tempo in BPM (default: 120)")
    p.add_argument("--bend-range", type=int, default=PITCH_BEND_RANGE,
                   help="Pitch bend range in semitones (default: 2)")
    args = p.parse_args()

    csv_to_midi(
        csv_path=args.csv_path,
        midi_path=args.output,
        channel=args.channel,
        tempo_bpm=args.tempo,
        bend_range=args.bend_range,
    )


if __name__ == "__main__":
    main()
