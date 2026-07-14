# Runtime Operation

Authoritative guide for actually **using** the system after it is deployed. If the fleet is not yet installed, start at [Fleet_Deployment_Guide.md](Fleet_Deployment_Guide.md) first.

> **Audience.** You (operator / researcher) driving a recording session from the Mac after the fleet has been set up.
> **Assumption.** The deployment phases (1–4) are done. Each Pi has `venv/`, code, models, and the two systemd user services running.

## 1. Preconditions

Before starting a session:

- Every Pi in [../start_recording_session.yaml](../start_recording_session.yaml) is powered up and reachable on the network.
- Autostart is enabled and both mic services are running on each Pi (`speech-record-mic1.service`, `speech-record-mic2.service`).
- The Mac has this repo checked out and Python dependencies installed on the Mac side:
  ```bash
  pip install python-osc PyYAML
  ```
- [../start_recording_session.yaml](../start_recording_session.yaml) is edited for the current session (which Pis, which mics, what processing to enable, whether to auto-start logging).

Every command below runs from the Mac in the repo root, not on the Pis.

## 2. Session Workflow (Command Line)

The normal live sequence:

```bash
# 1. Preflight: verify every expected Pi/mic is alive and reports audio ok.
python speech_control.py test start_recording_session.yaml

# 2. Start the session. Runs the same preflight, then sends startup commands
#    (vad_on / prosody_on / emotion_off / osc_start / osc_send_hz / log_start).
python speech_control.py start-recording-session start_recording_session.yaml

# 3. Pause / resume during the run.
python speech_control.py broadcast --session start_recording_session.yaml log_pause
python speech_control.py broadcast --session start_recording_session.yaml log_resume

# 4a. Save.
python speech_control.py broadcast --session start_recording_session.yaml log_save_stop take_001

# 4b. Or discard (do not save anything to disk).
python speech_control.py broadcast --session start_recording_session.yaml log_discard_stop
```

When you save, each Pi writes files locally under its configured `output_dir` (usually `log_data/`). The save command automatically appends Pi/mic identifiers to the filename so two processes never overwrite each other:

```
take_001_rpi5-11-1.csv
take_001_rpi5-11-2.csv
take_001_rpi5-12-1.csv
...
```

Details on every command, the YAML shape, and Python integration examples: [operator_osc_control.md](operator_osc_control.md).

## 3. Live Monitoring (Browser GUI)

For a visual view of what each Pi/mic is doing in real time:

```bash
./run_web.sh --session start_recording_session.yaml
```

Open the URL it prints (usually `http://localhost:3000/`). The GUI shows the union of:

- expected processes from `start_recording_session.yaml`
- live `/hello` heartbeats from the Pis

Expected processes that are silent appear in red — quick way to spot a down Pi or dead mic. The GUI is **not** required for the recording workflow; it is purely for visibility.

If the GUI shows stale processes or ignores `--session`, an old bridge instance is still bound to the ports — see Section 5 (Fresh local reset).

## 4. Data Collection

Saving with `log_save_stop` writes CSV files on **each Pi** individually. To collect them all to the Mac in one step:

```bash
python save_and_pull_logs.py --session start_recording_session.yaml take_001
```

This sends `save` to every expected process, waits for per-file acknowledgments, and pulls the files back over SSH into `log_data/pulled/<timestamp>/<pi-id>/`.

For the full save/collect protocol, CSV file layout, and per-feature column reference, see [Data_Collection_and_CSV_Format.md](Data_Collection_and_CSV_Format.md).

## 5. Fresh Local Reset (Before Testing on the Mac)

When you run the receiver/bridge or the local test launcher on the Mac repeatedly, stale processes can hold ports (`9000`, `8765`, `3000`) or send phantom `local-*` heartbeats. Reset them before starting a fresh test:

```bash
./fresh_start_local.sh --dry-run     # show what would be killed
./fresh_start_local.sh               # actually kill
./run_web.sh --replace --session start_recording_session.yaml
```

Scoped resets when you don't want to nuke everything:

- `./fresh_start_local.sh --mics-only` — only local `strip_monitor.py` processes.
- `./fresh_start_local.sh --bridge-only` — only bridge/listener processes.

Legacy compatibility: [../stop_two_mics.sh](../stop_two_mics.sh) is now a thin wrapper for `--mics-only`.

This reset workflow is Mac-side only. It never touches the fleet Pis.

## 6. Manual Launch And One-Machine Test

You need these paths only in three situations: bring-up before autostart is enabled, targeted debugging on a single Pi, or pure local testing on the Mac.

### 6.1 Manual launch on a Pi (autostart not yet enabled)

```bash
# On the Pi:
cd /home/pi/SPEECH_RECORD_ANALYSIS
source venv/bin/activate
./START_AUDIO_PROCESSING.sh          # standard two-mic runtime
tail -f logs/mic1.log logs/mic2.log
./stop_two_mics.sh                   # stop
```

**Never run this on a Pi where autostart is already active** — you will get duplicate processes fighting over the USB microphones and ports.

### 6.2 Local one-machine test (no fleet)

```bash
# On the Mac:
./START_LOCAL_TEST_PROCESSING.sh
```

Uses [../config_local_mic1.yaml](../config_local_mic1.yaml) and [../config_local_mic2.yaml](../config_local_mic2.yaml) — intentionally different from the real Pi configs (see [pi_runtime_processing.md § local vs real](pi_runtime_processing.md#21-local-vs-real-config-differences)).

Compact runbook covering both laptop-only and laptop+one-Pi test recipes: [quick_test_laptop_one_pi.md](quick_test_laptop_one_pi.md).

## 7. Runtime Troubleshooting Index

| Symptom                                                                  | Where to look                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `speech_control.py test` reports `TIMEOUT` for a Pi                      | Pi is off, network is down, or the mic service crashed. SSH in, `systemctl --user status speech-record-mic1.service`, then [Autostart_Configuration_on_Fleet.md § Verifying and debugging](Autostart_Configuration_on_Fleet.md#4-verifying-and-debugging). |
| Preflight reports `audio: failure` on a mic                              | USB mic disconnected, or [../config_mic1.yaml](../config_mic1.yaml) / [../config_mic2.yaml](../config_mic2.yaml) `audio_device` name mismatch. See [pi_runtime_processing.md § one-time checks](pi_runtime_processing.md#3-one-time-checks-on-each-pi).    |
| Browser GUI ignores `--session` or shows old processes                   | Stale bridge process. Run `./fresh_start_local.sh --bridge-only`.                                                                                                                                                                                          |
| `log_save_stop` succeeds but no file pulled back                         | Use `save_and_pull_logs.py` (Section 4) instead of raw `broadcast`. Raw broadcast saves locally on the Pi but does not fetch.                                                                                                                              |
| Files pulled back but timestamps look off                                | Pi clocks may not be NTP-synced. Verify with `ssh pi@<ip> 'date -u'`.                                                                                                                                                                                      |
| `journalctl --user -u speech-record-mic1.service` shows repeated crashes | Feature module error or config mismatch. See [Autostart_Configuration_on_Fleet.md § Logs](Autostart_Configuration_on_Fleet.md#42-logs).                                                                                                                    |

## 8. Related Docs

- [operator_osc_control.md](operator_osc_control.md) — every OSC operator command, full YAML shape, Python integration.
- [pi_runtime_processing.md](pi_runtime_processing.md) — per-Pi config internals, mic naming, launcher variants.
- [Data_Collection_and_CSV_Format.md](Data_Collection_and_CSV_Format.md) — save/pull protocol, CSV file schema, per-feature columns.
- [openSmile_information.md](openSmile_information.md) — openSMILE low-level descriptor reference.
- [quick_test_laptop_one_pi.md](quick_test_laptop_one_pi.md) — bring-up and dev test recipes.
- [Fleet_Deployment_Guide.md](Fleet_Deployment_Guide.md) — for deployment questions rather than runtime.
