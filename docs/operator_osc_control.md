# Operator Recording Control

For a full script/domain overview and preferred entrypoints, see [script_map.md](script_map.md).

This is the operator manual for starting and controlling a recording session from the command line.

## Execution Context (Read First)

All commands in this guide are run on the control computer, from the local
repository folder, not on each Raspberry Pi.

Assumption for this workflow:

- The Pi mic processes are already running (for example, from autostart services).

Before running the commands below, the control computer must have:

- This repository folder available locally (for example, `SPEECH_RECORD_ANALYSIS`).
- Required files in that folder:
  - `speech_control.py`
  - `start_recording_session.yaml`
  - `run_web.sh` and `receiver/` (only if you use the optional GUI)
- Python libraries:
  - `python-osc` (required)
  - `PyYAML` (recommended for full YAML parsing)
- Optional GUI runtime: Node.js + npm (needed only for `./run_web.sh`)

Install Python dependencies on the control computer if needed:

```bash
pip install python-osc PyYAML
```

## Fresh Local Reset (Recommended For Testing)

Before launching receiver/GUI during local testing, run
[../fresh_start_local.sh](../fresh_start_local.sh) to clear stale local
`strip_monitor.py` and bridge processes.

```bash
./fresh_start_local.sh --dry-run
./fresh_start_local.sh
./run_web.sh --replace --session start_recording_session.yaml
```

Partial reset options:

- `./fresh_start_local.sh --bridge-only` keeps local mic processes running.
- `./fresh_start_local.sh --mics-only` keeps the bridge untouched.
- `./stop_two_mics.sh` remains as a compatibility wrapper for `--mics-only`.

This prevents stale local heartbeats from appearing as `local-*` and avoids
port conflicts where a previous bridge instance makes `--session` look ignored.
See also [pi_runtime_processing.md](pi_runtime_processing.md).

For the exact short workflows used in practice (laptop-only and control-laptop +
one Pi), see [quick_test_laptop_one_pi.md](quick_test_laptop_one_pi.md).

The normal workflow is:

```bash
# 1. Test the expected recording rig.
python speech_control.py test start_recording_session.yaml

# 2. Start the session if the test is OK.
python speech_control.py start-recording-session start_recording_session.yaml

# 3. Pause or resume during the run if needed.
python speech_control.py broadcast --session start_recording_session.yaml log_pause
python speech_control.py broadcast --session start_recording_session.yaml log_resume

# 4. Save the run.
python speech_control.py broadcast --session start_recording_session.yaml log_save_stop session_001.csv

# Or discard the run instead.
# python speech_control.py broadcast --session start_recording_session.yaml log_discard_stop
```

The browser GUI is not required for this workflow. The script talks directly to the Pi processes listed in `start_recording_session.yaml`.

## GUI With Expected Rig

You can also launch the web GUI with the same expected YAML list:

```bash
./run_web.sh --session start_recording_session.yaml
```

In this mode, the GUI process list is the union of:

- expected processes from the YAML file
- live heartbeats received from `/hello`

Expected processes that are not currently connected are shown as missing (red), so you can immediately see which Pi/mic is down.

## What `test` Does

`test` reports the state of the system.

With a YAML file:

```bash
python speech_control.py test start_recording_session.yaml
```

the script treats the YAML file as the expected rig. It tests every listed Pi/mic process directly. For each expected process it sends `/ctrl/query_state` to the configured `IP:port` and reports `OK`, `ERROR`, or `TIMEOUT`.

This is the command to run before a real recording session. If one Pi is off, one mic process died, or one microphone reports `audio=failure`, the test fails and tells you which expected process is bad.

Without a YAML file:

```bash
python speech_control.py test
```

the script listens briefly for `/hello` heartbeats and reports whatever processes are currently present. This is useful for discovery: it tells you what is there, but it does not know what should be there.

## Start Recording

Use this command for the real session:

```bash
python speech_control.py start-recording-session start_recording_session.yaml
```

It does two things:

1. It runs the same exact-rig test as `python speech_control.py test start_recording_session.yaml`.
2. Only if every expected Pi/mic is OK, it sends the startup commands described in the YAML file.

With the current `start_recording_session.yaml`, startup sends:

```text
vad_on
prosody_on
emotion_off
osc_start
osc_send_hz 10
log_start
```

If any expected process fails the preflight, recording does not start. The command stops before sending `log_start`.

## Pause, Resume, Save, Or Discard

After the session has started, use `broadcast --session ...` so every command goes to the same curated Pi/mic list.

Pause:

```bash
python speech_control.py broadcast --session start_recording_session.yaml log_pause
```

Resume:

```bash
python speech_control.py broadcast --session start_recording_session.yaml log_resume
```

Save:

```bash
python speech_control.py broadcast --session start_recording_session.yaml log_save_stop session_001.csv
```

Discard:

```bash
python speech_control.py broadcast --session start_recording_session.yaml log_discard_stop
```

When saving, the script automatically adds the Pi/mic id to each filename so two processes do not overwrite each other. For example, `session_001.csv` becomes names like:

```text
session_001_rpi5-11-1.csv
session_001_rpi5-11-2.csv
session_001_rpi5-12-1.csv
session_001_rpi5-12-2.csv
```

Each Pi writes its files locally under its configured `output_dir`, normally `log_data/`.

## The Session YAML

Edit `start_recording_session.yaml` before the demonstration or recording session.

The important part is the expected rig:

```yaml
pis:
  - pi_id: rpi5-11
    ip: 192.168.0.11
    mics: [1, 2]

  - pi_id: rpi5-12
    ip: 192.168.0.12
    mics: [1, 2]
```

For each Pi, `mics: [1, 2]` means:

| Mic | Control Target |
| --- | -------------- |
| `1` | `IP:9001`      |
| `2` | `IP:9002`      |

With six Pis and two mics per Pi, the YAML describes 12 expected recording processes.

The YAML also controls what gets turned on before logging starts:

```yaml
processing:
  vad: true
  prosody: true
  emotion: false

osc:
  active: true
  send_hz: 10

logging:
  start: true
```

`true` sends the corresponding `_on` command. `false` sends the corresponding `_off` command. `logging.start: true` sends `log_start` after all expected processes pass the test.

## Requirements

For the command-line workflow:

- all commands are executed on the control computer (repo root)
- the Pi audio processes must already be running
- the central computer must be able to reach the Pi IP addresses in the YAML
- the active Python environment must have `python-osc` (and preferably `PyYAML`)

Install the dependency if needed:

```bash
pip install python-osc
```

The exact-rig workflow does not require the browser GUI or `./run_web.sh`.

## Dry Run

Dry run prints what would be sent without sending OSC packets.

Test the configured rig list without sending:

```bash
python speech_control.py test --dry-run start_recording_session.yaml
```

Preview the startup sequence:

```bash
python speech_control.py start-recording-session --dry-run start_recording_session.yaml
```

Preview a save:

```bash
python speech_control.py broadcast --session start_recording_session.yaml --dry-run log_save_stop session_001.csv
```

## What Gets Saved

The exact enabled files depend on `config_features.yaml`, but the standard saved set is:

| File suffix          | Contents                                                                      |
| -------------------- | ----------------------------------------------------------------------------- |
| `.csv`               | Combined/status table for quick inspection.                                   |
| `_opensmile_lld.csv` | Standard-style openSMILE LLD table: name, frameTime, Unix interval, features. |
| `_vad.csv`           | Alignable VAD timeline: name, frameTime, Unix interval, vad.                  |
| `_emotion.csv`       | Alignable emotion windows with labels, confidence, and scores.                |

For `_opensmile_lld.csv`, the feature columns come from `opensmile.log_features` in `config_features.yaml`. With the default `log_features: all`, the saved file includes the full configured openSMILE LLD table. The shorter `opensmile.osc_features` list is only for live GUI/OSC display.

Use `frameTime`, `unix_start`, and `unix_end` for the separate openSMILE, VAD, and emotion files. The legacy combined/status CSV still includes project-specific timing columns:

```text
sample_start, sample_end, sample_center, start_s, end_s, time_s
```

Do not use central-computer receive time for research alignment.

For alignment and quick plotting, copy [../log_data/alignment_template.yaml](../log_data/alignment_template.yaml), fill in the files from one run, then run:

```bash
cd log_data
python align_visualize.py alignment.yaml
```

## Optional GUI

The browser GUI is optional. It is useful for watching live telemetry and seeing a terminal-style OSC log, but it is not required to run the exact-rig session commands above.

Start the GUI bridge only when you want the browser view:

```bash
./run_web.sh
```

Then open:

```text
http://localhost:3000/
```

The GUI bridge keeps its own live list of `/hello` heartbeats. That is useful for monitoring, but the recording-session commands should still use `start_recording_session.yaml` when you need to prove that the full expected rig is present.

## Command Reference

Common commands:

| Command                     | Meaning                                        |
| --------------------------- | ---------------------------------------------- |
| `query_state`               | Ask a process to report state. Used by `test`. |
| `log_start`                 | Start a RAM-backed logging session.            |
| `log_pause`                 | Pause appending rows to the RAM session.       |
| `log_resume`                | Resume appending rows.                         |
| `log_save_stop`             | Stop and write files.                          |
| `log_discard_stop`          | Stop and discard the RAM session.              |
| `vad_on`, `vad_off`         | Toggle VAD.                                    |
| `prosody_on`, `prosody_off` | Toggle openSMILE/prosody.                      |
| `emotion_on`, `emotion_off` | Toggle emotion inference.                      |
| `osc_start`, `osc_stop`     | Toggle live OSC telemetry.                     |
| `osc_send_hz`               | Set live OSC telemetry rate.                   |
| `audio_reconnect`           | Retry opening the configured microphone.       |

## Python Integration

The same functionality can be imported from Python.

Test the exact rig:

```python
from speech_control import test_session_file, ack_ready_for_recording

acks = test_session_file("start_recording_session.yaml")
if not acks or any(not ack_ready_for_recording(a) for a in acks):
    raise RuntimeError("one or more mic processes are not ready")
```

Start the YAML-defined recording session:

```python
from speech_control import load_session_plan, start_recording_session, ack_ready_for_recording

plan = load_session_plan("start_recording_session.yaml")
acks = start_recording_session("start_recording_session.yaml")
preflight = acks[:len(plan.targets)]
commands = acks[len(plan.targets):]

if not preflight or any(not ack_ready_for_recording(a) for a in preflight) or any(not a.ok for a in commands):
    raise RuntimeError("recording session did not start cleanly")
```

Send a command to the curated target list:

```python
from speech_control import load_session_plan, broadcast_ctrl

plan = load_session_plan("start_recording_session.yaml")
acks = broadcast_ctrl(plan.targets, "log_pause", only_audio_ok=False)
if not all(a.ok for a in acks):
    raise RuntimeError("at least one expected process did not acknowledge log_pause")
```
