# Phase 2 — Test Deployment on One Fleet Pi

Part of the [Fleet Deployment Guide](Fleet_Deployment_Guide.md). This document covers **Phase 2** only: pushing the complete bundle from the Mac to a single fleet Pi, installing it offline, and verifying the whole stack (system libraries, Python env, audio capture, OSC to Mac) end to end — **without** enabling autostart.

Do this once, on one Pi, before doing Phase 3 on all six. Fixing a bundle problem here is a one-line edit; fixing it after a fleet-wide deploy is six times the pain.

## 0. Prerequisites

- Phase 1 is complete. The Mac holds a full bundle: `src/` + `models/` + `wheelhouse/` + `debs/`. Verify:
  ```bash
  ls wheelhouse/*.whl | wc -l    # non-zero
  ls debs/*.deb      | wc -l     # non-zero
  ```
- The Mac is on the fleet network (either wired to the same switch or on the same Wi-Fi that the Pis are on).
- [../devices.csv](../devices.csv) contains the fleet Pi mapping and its IPs are correct for the current network.

Example target for this document: **Pi #1**, hostname `rpi5-11`, IP `192.168.0.11`, SSH user `pi`. Replace with whichever Pi you actually want to test.

## 1. Network Sanity Check

On the Mac:

```bash
ping -c 3 192.168.0.11
```

Expected: three `64 bytes from 192.168.0.11 ...` lines. If you see `Request timeout` or `No route to host`:

- Check the Pi is powered up.
- Check your Mac is actually on the fleet subnet (`ifconfig | grep 'inet 192.168.0'` should show your Mac's IP on the same `/24`).
- Check the router or switch is up.

Do not continue until ping works. Everything below relies on SSH, which relies on IP reachability.

## 2. Push The Bundle And Install

One command from the Mac (from the repo root) does both the rsync and the remote install:

```bash
./deploy_bundle_to_fleet.py --user pi --devices 1
```

What it does, in order:

1. Reads [../devices.csv](../devices.csv), picks device index 1 (currently `rpi5-11` / `192.168.0.11`).
2. `rsync -az --delete` your local repo (minus `.git/`, `venv/`, `.wheelhouse-venv/`, `__pycache__/`) to `/home/pi/SPEECH_RECORD_ANALYSIS/` on that Pi.
3. Runs `install_from_bundle.sh` remotely over SSH.

The `install_from_bundle.sh` step:

- Checks the bundle looks complete (models, wheelhouse, debs).
- Installs the `.deb`s from `./debs/*.deb` with `apt` in offline mode (no network access).
- Calls `setup_pi.sh` with `SKIP_APT=1`, which builds `venv/` and installs every Python package from `./wheelhouse/` with `--no-index`.

Expected end-of-run summary:

```
Summary:
  Sync ok: 1:192.168.0.11
  Sync failed: none
  Install ok: 1:192.168.0.11
  Install failed: none
```

If the run stops at password prompts, either use `ssh-copy-id` first (one-time, per Pi) or export `SSHPASS` and use `sshpass` — this is documented in [Fleet_Deployment_via_SSH.md § SSH keys](Fleet_Deployment_via_SSH.md#3-set-up-ssh-keys-recommended).

## 3. Verify Audio Devices

SSH into the Pi and confirm both microphones are visible:

```bash
ssh pi@192.168.0.11
cd /home/pi/SPEECH_RECORD_ANALYSIS
source venv/bin/activate
python strip_monitor.py --list-devices
```

Expected: at least two USB audio input devices whose names match the ones referenced in [../config_mic1.yaml](../config_mic1.yaml) and [../config_mic2.yaml](../config_mic2.yaml) (typically `MIC1` and `MIC2`, or the default device index).

If a mic is missing, unplug/replug the USB cable and re-run. If the names differ from the configs, update the `audio_device` field in the two YAMLs before continuing — the launcher will otherwise fail to open the stream.

## 4. Run The Mic Pipeline Manually

Still on the Pi, launch the standard two-mic runtime **without** autostart:

```bash
./START_AUDIO_PROCESSING.sh
```

Expected:

- Startup logs from both `strip_monitor.py` processes.
- PyTorch + FunASR model load (5–20 seconds, one-time per boot).
- Continuous chunk-processing log lines (VAD, features, optional emotion inference).
- No traceback errors.

Speak into a mic — you should see feature values change in the log stream.

Stop with `Ctrl+C`. If it survived >30 seconds without errors, the Pi is functional. Move to the OSC test.

## 5. Verify OSC Stream To Mac

The Pi sends live OSC telemetry to the Mac. This step confirms the end-to-end path.

**5.1 Check the OSC target on the Pi.**
[../config_features.yaml](../config_features.yaml) must have `osc_ip` set to your Mac's IP on this network. Check your Mac's IP with `ifconfig | grep 'inet 192.168.0'` and confirm the YAML matches. If it does not, edit and re-run the launcher.

**5.2 On the Mac**, start the receiver (in a separate terminal from your SSH session):

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS
./run_web.sh --session start_recording_session.yaml
```

Open the URL it prints (usually `http://localhost:3000`).

**5.3 On the Pi**, start the mic pipeline again:

```bash
./START_AUDIO_PROCESSING.sh
```

**5.4 Expected result.**
Within a few seconds the browser GUI should show `rpi5-11` with `mic 1` and `mic 2` reporting `audio: ok`. Talking into either mic should make feature values move.

If nothing arrives on the Mac:

- Confirm the Pi and Mac are on the same subnet.
- Confirm no host-based firewall on the Mac is blocking UDP 9000.
- Confirm `osc_ip` / `osc_port` in `config_features.yaml` on the Pi match what `run_web.sh` binds to on the Mac.

## 6. Stop Cleanly

On the Pi:

```bash
./stop_two_mics.sh
```

Then log out (`exit`). At this point:

- The Pi is fully deployed but idle (no autostart).
- The bundle is validated end-to-end.
- You can safely proceed to [Phase 3 — Fleet Rollout via SSH](Fleet_Deployment_via_SSH.md).

Do **not** run `configure_auto_start.py` yet. Autostart is Phase 4, opt-in, only after every Pi passes this same manual test.

## 7. Common Failures And Fixes

| Symptom                                                                 | Likely cause                                                            | Fix                                                                                                      |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `install_from_bundle.sh` aborts on "missing `.deb` for `libportaudio2`" | `debs/` did not sync (was empty on the Mac)                             | Re-run Phase 1 Section 4; verify `ls debs/*.deb` on the Mac before deploying.                            |
| `pip install` fails with `no matching distribution for torch`           | wheel for the Pi's exact Python version is missing                      | Rebuild the wheelhouse on a builder Pi running the **same** Python version as the fleet.                 |
| `strip_monitor.py --list-devices` shows no USB mics                     | USB cable / hub problem, or the Pi did not enumerate the device on boot | Physically re-plug, or `sudo systemctl restart alsa-state`.                                              |
| Launcher runs but OSC never reaches the Mac                             | wrong `osc_ip` in `config_features.yaml`, or firewall on Mac            | Fix the YAML, or allow UDP 9000 on the Mac.                                                              |
| SSH prompts for password every time                                     | no key-based auth set up                                                | See [Fleet_Deployment_via_SSH.md § SSH keys](Fleet_Deployment_via_SSH.md#3-set-up-ssh-keys-recommended). |
