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

| Topic | File |
| --- | --- |
| Script responsibilities and entrypoints | [docs/script_map.md](docs/script_map.md) |
| Fleet deployment (end-to-end) | [docs/main_deployment.md](docs/main_deployment.md) |
| Recording operator commands | [docs/operator_osc_control.md](docs/operator_osc_control.md) |
| Pi runtime setup and run | [docs/pi_runtime_processing.md](docs/pi_runtime_processing.md) |
| Autostart service install details | [docs/pi_autostart_installation.md](docs/pi_autostart_installation.md) |
| Central CSV collection and data semantics | [docs/central_collection.md](docs/central_collection.md) |
| openSMILE reference notes | [docs/openSmile_information.md](docs/openSmile_information.md) |

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
