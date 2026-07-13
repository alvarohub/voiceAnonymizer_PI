# Next Steps

Last updated: 2026-07-09

## 1) Before copying to Pi (on this Mac)

- Confirm local wheelhouse is deleted.
- Confirm key file changes are present:
  - `requirements-pi.txt` now includes `scipy`.
  - `strip_monitor.py` has legacy comments for display path.
  - `src/prosody.py` has legacy comments for pyworld fallback.

Suggested check:

```bash
git status --short
```

## 2) Copy project folder to Pi (USB key workflow)

- Copy the full `SPEECH_RECORD_ANALYSIS/` folder to the Pi target path.
- Typical target path expected by docs/scripts:
  - `/home/pi/SPEECH_RECORD_ANALYSIS`
  - or `/home/admin/SPEECH_RECORD_ANALYSIS` (if using admin account)

## 3) Rebuild wheelhouse on Pi

Run on Pi from the project root:

```bash
cd ~/SPEECH_RECORD_ANALYSIS
chmod +x prepare_wheelhouse.sh
./prepare_wheelhouse.sh
```

## 4) Verify wheelhouse was built correctly

```bash
find wheelhouse -type f -name '*.whl' | wc -l
ls -lh wheelhouse | head
```

Optional spot checks:

```bash
ls wheelhouse | grep -E 'scipy|opensmile|torch|torchaudio|funasr'
```

## 5) Quick runtime sanity on Pi (headless)

```bash
python3 strip_monitor.py --config config_mic1.yaml --features-config config_features.yaml --no-display
```

If successful, stop and repeat for mic2 config.

## 6) Continue with your usual deployment flow

- If this Pi is your build source, use its wheelhouse for fleet distribution.
- Keep current flat repo layout for now (reshuffle deferred due risk/time).

## 7) Optional cleanup after successful run

- Commit current local changes with a clear message.
- Tag this state as the pre-fleet baseline.
