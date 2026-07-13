# Pi Runtime Processing Guide

This guide is for configuring and running microphone processing on Pis and verifying the central receiver path.

## 1. What Runs Where

| Machine                    | Process                                              | Typical command                                                           |
| -------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------- |
| Raspberry Pi               | Two `strip_monitor.py` processes, one per microphone | `./START_AUDIO_PROCESSING.sh`                                             |
| Central computer           | Browser receiver and OSC-to-WebSocket bridge         | `./run_web.sh --session start_recording_session.yaml`                     |
| Central computer, optional | Central OSC CSV collector                            | `python osc_collector.py --bind 0.0.0.0 --port 9000 --out log_data/multi` |

For overall script responsibilities, see [script_map.md](script_map.md).

## 2. Runtime Config Files

Core runtime config files are:

- `config_mic1.yaml`: mic-1 process settings
- `config_mic2.yaml`: mic-2 process settings
- `config_features.yaml`: research feature/log settings shared by both mics

Key fields to verify per Pi:

- `pi_id`
- `mic_id`
- `audio_device`
- `ctrl_port`
- `osc_ip`
- `osc_port`

Recommended conventions:

- mic 1 uses `ctrl_port: 9001`
- mic 2 uses `ctrl_port: 9002`
- `audio_device` values should match your real microphone names (`MIC1` / `MIC2` conventions)

### 2.1 Local vs real config differences

The launchers are intentionally split:

- `./START_AUDIO_PROCESSING.sh` uses real Pi configs: `config_mic1.yaml`, `config_mic2.yaml`
- `./START_LOCAL_TEST_PROCESSING.sh` uses local test configs: `config_local_mic1.yaml`, `config_local_mic2.yaml`

Intentional local-only differences:

- `pi_id: local` in local configs (explicit local-test identity in GUI)
- `config_local_mic1.yaml` uses `audio_device: null` (system default mic)
- `config_local_mic2.yaml` uses `audio_device: 'MIC2'` on purpose so local mode shows an expected second stream that usually reports audio failure
- `emotion_load: false` in local configs for lighter startup during one-machine tests

For real Pi deployment, keep using `config_mic1.yaml` and `config_mic2.yaml`.
In those real configs, keep `pi_id: null` unless you explicitly want a fixed numeric/string `pi_id`.

## 3. One-Time Checks On Each Pi

From project root on the Pi:

```bash
source venv/bin/activate
python strip_monitor.py --list-devices
```

Confirm both microphones are visible and that names used in config files resolve correctly.

## 4. Start And Stop Processing On Pi

Start standard two-mic runtime:

```bash
./START_AUDIO_PROCESSING.sh
```

Stop runtime:

```bash
./stop_two_mics.sh
```

Manual/advanced single-process start remains available via `start_audio_server.sh`.

## 5. Central Receiver

From project root on the control machine:

```bash
./run_web.sh --session start_recording_session.yaml
```

In this mode, expected session targets come from `start_recording_session.yaml`, and missing expected processes are shown in red in the GUI.

### 5.1 Fresh local reset before launching receiver

For local testing, run [../fresh_start_local.sh](../fresh_start_local.sh) first so stale
local `strip_monitor.py` or bridge processes do not contaminate the GUI view.

Recommended sequence:

```bash
./fresh_start_local.sh --dry-run
./fresh_start_local.sh
./run_web.sh --replace --session start_recording_session.yaml
```

Partial cleanup options:

- `./fresh_start_local.sh --bridge-only` keeps local mics running and only resets receiver/bridge listeners.
- `./fresh_start_local.sh --mics-only` keeps the bridge untouched and only stops local mic processes.
- `./stop_two_mics.sh` remains available as a compatibility wrapper for `--mics-only`.

This prevents old local heartbeat senders from showing up as unexpected `local-*`
streams and avoids port conflicts on `9000`/`8765`/`3000`.

For a concrete two-scenario runbook (laptop-only and control-laptop + one Pi), see
[quick_test_laptop_one_pi.md](quick_test_laptop_one_pi.md).

## 6. Session Control (Operator Path)

For the full command workflow (test/start/pause/resume/save/discard), use:

- [operator_osc_control.md](operator_osc_control.md)

## 7. Data Collection

For central CSV capture, Pi-local logs, timestamps, and feature meanings, use:

- [central_collection.md](central_collection.md)

## 8. Diagnostics

Quick checks:

```bash
python diag_audio.py
python speech_control.py test start_recording_session.yaml
```

If needed, gather Pi logs back to control machine:

```bash
./gather_logs.sh log_data/session_001 pi1.local pi2.local
```

## 9. openSMILE Notes

For openSMILE-specific background and references, use:

- [openSmile_information.md](openSmile_information.md)
