"""Quick audio-path diagnostic for the Pi.

Captures 10 s from MIC2 (or whichever device matches --device),
prints RMS / peak levels for each raw 48 kHz channel AND for the
resampled-to-16 kHz mono signal we feed into the streamer pipeline.

Then runs Silero VAD on the resampled audio and prints what it found.

If RMS is healthy but VAD finds nothing, the VAD / model path is suspect.
If RMS is near zero, the mic / channel / resample path is suspect.

Usage on the Pi:
    cd ~/SPEECH_RECORD_ANALYSIS
    source venv/bin/activate
    python diag_audio.py
    # or
    python diag_audio.py --device MIC2 --seconds 10
"""
import argparse
import sys
import numpy as np
import sounddevice as sd


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--device", default="MIC2",
                   help="Substring match against sd.query_devices() name")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--target-sr", type=int, default=16000)
    args = p.parse_args()

    # 1. Resolve device
    devs = sd.query_devices()
    dev_idx = None
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0 and args.device.lower() in d["name"].lower():
            dev_idx = i
            break
    if dev_idx is None:
        print(f"[ERR] No input device matching {args.device!r}. Available:")
        for i, d in enumerate(devs):
            if d["max_input_channels"] > 0:
                print(f"  [{i}] {d['name']}  ch={d['max_input_channels']}  "
                      f"sr={int(d['default_samplerate'])}")
        sys.exit(1)

    dev = devs[dev_idx]
    native_sr = int(dev["default_samplerate"])
    channels = int(dev["max_input_channels"])
    print(f"Device: [{dev_idx}] {dev['name']}")
    print(f"  native_sr={native_sr}, channels={channels}")
    print(f"  capturing {args.seconds:.1f}s, will resample to {args.target_sr} Hz")
    print()
    print("Speak NOW (talk loudly, say a few sentences):")

    # 2. Blocking capture (all channels, native rate)
    frames = int(args.seconds * native_sr)
    rec = sd.rec(frames, samplerate=native_sr, channels=channels,
                 dtype="float32", device=dev_idx)
    sd.wait()
    print("Capture done.\n")

    # 3. Per-channel stats at native rate
    print("=== Raw audio at native rate ===")
    for ch in range(channels):
        x = rec[:, ch]
        rms = float(np.sqrt(np.mean(x ** 2)))
        peak = float(np.max(np.abs(x)))
        dbfs = 20 * np.log10(rms + 1e-12)
        print(f"  channel {ch}: RMS={rms:.5f}  peak={peak:.5f}  RMS={dbfs:+.1f} dBFS")
    print()

    # 4. Mono (channel 0) → resample to target_sr, same path as strip_monitor.py
    print("=== Resampled mono (channel 0 → target_sr) ===")
    mono = rec[:, 0]
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(args.target_sr, native_sr)
        up, down = args.target_sr // g, native_sr // g
        resampled = resample_poly(mono, up, down).astype(np.float32)
        print(f"  resample_poly up={up} down={down}  "
              f"in={len(mono)} samples  out={len(resampled)} samples")
    except ImportError:
        print("  scipy not available — falling back to naive decimation")
        ratio = native_sr // args.target_sr
        resampled = mono[::ratio].astype(np.float32)

    rms = float(np.sqrt(np.mean(resampled ** 2)))
    peak = float(np.max(np.abs(resampled)))
    dbfs = 20 * np.log10(rms + 1e-12)
    print(f"  RMS={rms:.5f}  peak={peak:.5f}  RMS={dbfs:+.1f} dBFS")
    print()

    if rms < 1e-4:
        print("[WARN] Signal is essentially silent. Suspect mic gain / channel.")
        print("       Try `arecord -D hw:2,0 -f S16_LE -r 48000 -c 2 -d 5 /tmp/t.wav`")
        print("       and inspect /tmp/t.wav.")
        return

    # 5. Run Silero VAD on the resampled audio
    print("=== Silero VAD ===")
    try:
        import torch
        print("  loading silero-vad…")
        model, utils = torch.hub.load("snakers4/silero-vad", "silero_vad",
                                      force_reload=False, trust_repo=True)
        get_speech_timestamps = utils[0]
        tensor = torch.from_numpy(resampled).float()
        for threshold in (0.5, 0.3, 0.15):
            stamps = get_speech_timestamps(tensor, model,
                                           threshold=threshold,
                                           sampling_rate=args.target_sr)
            total_speech = sum((s["end"] - s["start"]) for s in stamps) / args.target_sr
            print(f"  threshold={threshold}: {len(stamps)} segments, "
                  f"{total_speech:.2f}s of speech")
        print()
        print("If threshold=0.5 found segments → VAD path is fine, increase "
              "vad_threshold or check downstream.")
        print("If even threshold=0.15 found nothing but RMS is non-trivial → "
              "audio shape/sign issue (clipping, DC offset, very noisy mic).")
    except Exception as e:
        print(f"  [ERR] VAD test failed: {e}")


if __name__ == "__main__":
    main()
