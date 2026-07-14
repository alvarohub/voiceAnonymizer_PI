# Script Map And Entrypoints

This document clarifies script responsibilities without moving files. For the end-to-end deployment story, start at [Fleet_Deployment_Guide.md](Fleet_Deployment_Guide.md).

## Domains

| Domain                    | Primary files                                                                                                                                                         | Purpose                                                                                    |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Deployment and install    | `prepare_wheelhouse.sh`, `prepare_debs.sh`, `install_from_bundle.sh`, `deploy_bundle_to_fleet.py`, `deploy_lab_defaults.sh`, `configure_auto_start.py`, `devices.csv` | Build offline wheels + debs, distribute bundle, install on Pis, set up autostart services. |
| Pi runtime processing     | `START_AUDIO_PROCESSING.sh`, `strip_monitor.py`, `audio_analysis_background.py`, `config_mic1.yaml`, `config_mic2.yaml`, `config_features.yaml`, `src/`               | Capture audio and run analysis pipeline on each Pi/mic process.                            |
| Control and communication | `speech_control.py`, `broadcast_ctrl.py`, `run_web.sh`, `receiver/`, `osc_collector.py`                                                                               | Start and control sessions, run GUI bridge, collect OSC streams.                           |
| Diagnostics and ops       | `diag_audio.py`, `gather_logs.sh`, `stop_two_mics.sh`                                                                                                                 | Check health, gather logs, stop processes safely.                                          |

## Preferred Entrypoints

Use these first. Other scripts are secondary/manual tools.

| Task                            | Preferred command                                                               | Notes                                                       |
| ------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Build offline dependency wheels | `./prepare_wheelhouse.sh`                                                       | Run on the builder Pi (Phase 1).                            |
| Build offline system `.deb` set | `./prepare_debs.sh`                                                             | Run on the builder Pi (Phase 1). Ships in a separate patch. |
| Fleet deploy with lab defaults  | `bash deploy_lab_defaults.sh`                                                   | Runs Phase 2+3+4 in sequence.                               |
| Fleet deploy, dry-run           | `bash deploy_lab_defaults.sh --dry-run`                                         | Virtual run of full deployment flow.                        |
| Deploy only bundle+install      | `python3 deploy_bundle_to_fleet.py ...`                                         | Phase 2+3 only.                                             |
| Configure autostart services    | `python3 configure_auto_start.py ...`                                           | Phase 4 only.                                               |
| Test expected recording rig     | `python speech_control.py test start_recording_session.yaml`                    | Exact-rig preflight before session start.                   |
| Start recording session         | `python speech_control.py start-recording-session start_recording_session.yaml` | Includes preflight test.                                    |
| Launch browser GUI              | `./run_web.sh --session start_recording_session.yaml`                           | Shows expected plus live process status.                    |
| Run Pi audio processing         | `./START_AUDIO_PROCESSING.sh`                                                   | Standard two-mic runtime on a Pi.                           |

## Secondary Utilities

These are useful but not primary entrypoints for normal operations:

- `setup_pi.sh`: lower-level environment setup script used by `install_from_bundle.sh`.
- `broadcast_ctrl.py`: direct OSC broadcast utility for ad-hoc control.
- `audio_analysis_background.py`: lower-level launcher/helper path.
- `start_audio_server.sh`: one-process/manual server start workflow.

## Safety Rule For Future Refactors

If files are moved later, keep root-level wrapper scripts with the same names so old commands continue to work until all docs and operator habits are migrated.
