# Speech Record Analysis

Raspberry Pi speech-analysis pipeline for live microphone capture, VAD/prosody/emotion inference, OSC telemetry, CSV logging, and browser-based monitoring.

This repository now uses focused docs by workflow. Start here, then jump to the specific guide you need.

## Quick Start By Role

### 1) I need to deploy or update the whole Pi fleet

Use:

- [docs/main_deployment.md](docs/main_deployment.md)

This covers wheelhouse build, SSH bundle distribution, remote install, and autostart enablement.

### 2) I need to run a recording session (operator flow)

Use:

- [docs/operator_osc_control.md](docs/operator_osc_control.md)

This covers exact-rig test, start session, pause/resume, save/discard, and GUI expected-rig mode.

### 3) I need to configure and run Pi processing

Use:

- [docs/pi_runtime_processing.md](docs/pi_runtime_processing.md)

This covers config files, normal Pi start/stop commands, central receiver launch, and diagnostics.

### 4) I need detailed autostart service guidance

Use:

- [docs/pi_autostart_installation.md](docs/pi_autostart_installation.md)

This is the systemd user-service focused guide.

## Documentation Map

| Topic                                     | File                                                                   |
| ----------------------------------------- | ---------------------------------------------------------------------- |
| Script responsibilities and entrypoints   | [docs/script_map.md](docs/script_map.md)                               |
| Fleet deployment (end-to-end)             | [docs/main_deployment.md](docs/main_deployment.md)                     |
| Recording operator commands               | [docs/operator_osc_control.md](docs/operator_osc_control.md)           |
| Pi runtime setup and run                  | [docs/pi_runtime_processing.md](docs/pi_runtime_processing.md)         |
| Autostart service install details         | [docs/pi_autostart_installation.md](docs/pi_autostart_installation.md) |
| Central CSV collection and data semantics | [docs/central_collection.md](docs/central_collection.md)               |
| openSMILE reference notes                 | [docs/openSmile_information.md](docs/openSmile_information.md)         |

## Control Computer Prerequisites (Before Operator Commands)

All recording-session commands in this repository are executed on the control
computer (Mac/Linux), not on each Pi.

Assumption for operator/session commands:

- The Pi mic processes are already running (normally via autostart, or manual start during bring-up).

Before running session-control commands, the control computer should have:

- A local project folder containing this repository (for example, `SPEECH_RECORD_ANALYSIS`).
- Required files present in that folder:
  - `speech_control.py`
  - `start_recording_session.yaml`
  - `run_web.sh` and `receiver/` (only if you use the browser GUI)
- Python libraries for operator CLI:
  - `python-osc` (required)
  - `PyYAML` (recommended for full YAML parsing)
- GUI runtime (optional): Node.js and npm for `./run_web.sh`

Install Python dependencies on the control computer if needed:

```bash
pip install python-osc PyYAML
```

## How It Works After Deployment

In the real deployed setup, Pi processes are started automatically at boot by
systemd user services. Operators do not manually launch microphone processes on
each boot.

- Each Pi runs two services: speech-record-mic1.service and speech-record-mic2.service.
- Those services launch strip_monitor.py with config_mic1.yaml or config_mic2.yaml, plus config_features.yaml.
- Linger is enabled so user services start without requiring an interactive login.

For setup details, use [docs/main_deployment.md](docs/main_deployment.md) and
[docs/pi_autostart_installation.md](docs/pi_autostart_installation.md).

Important rule once autostart is enabled:

- Do not run START_AUDIO_PROCESSING.sh manually on top of active services, because it can create duplicate processes.

## Manual Launch Before Autostart Is Set

Use manual launch only during bring-up or testing before services are installed.

On each Pi:

```bash
cd /home/pi/SPEECH_RECORD_ANALYSIS
bash install_from_bundle.sh
./START_AUDIO_PROCESSING.sh
```

Inspect logs:

```bash
tail -f logs/mic1.log logs/mic2.log
```

Stop manual processes:

```bash
./stop_two_mics.sh
```

## Real vs Local Test Launchers

Normal real-setting launcher:

- START_AUDIO_PROCESSING.sh
- Uses config_mic1.yaml and config_mic2.yaml

One-machine local-test launcher:

- START_LOCAL_TEST_PROCESSING.sh
- Uses config_local_mic1.yaml and config_local_mic2.yaml

So the primary switch between real setting and local test mode is which START
script you run.

## Most Common Commands

From deployment/control machine:

```bash
# Full deploy using lab defaults (Phase 2+3+4)
bash deploy_lab_defaults.sh

# Virtual run before touching devices
bash deploy_lab_defaults.sh --dry-run

# Exact-rig preflight before recording
python speech_control.py test start_recording_session.yaml

# GUI with expected rig list
./run_web.sh --session start_recording_session.yaml
```

From each Raspberry Pi:

```bash
# Offline bundle install
bash install_from_bundle.sh

# Standard two-mic runtime
./START_AUDIO_PROCESSING.sh

# Stop both mic processes
./stop_two_mics.sh
```

## Important Separation

- `devices.csv` is deployment inventory (install/autostart target list).
- `start_recording_session.yaml` is recording-session inventory (which processes are expected/controlled during a run).

Keeping those files separate is intentional and prevents deployment-vs-session mistakes.

## Repository Notes

No file moves are required for current workflows. The project is still root-script based; clarity is provided through workflow docs and script mapping rather than folder restructuring.
