# Script Map And Entrypoints

This document clarifies script responsibilities without moving files. For the end-to-end deployment story, start at [Fleet_Deployment_Guide.md](Fleet_Deployment_Guide.md).

## Domains

| Domain                    | Primary files                                                                                                                                                                                                                                                                                    | Purpose                                                                                                           |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Deployment and install    | `prepare_wheelhouse.sh`, `prepare_debs.sh`, `install_from_bundle.sh`, `uninstall_from_bundle.sh`, `copy_bundle_to_targets.py`, `install_bundle_on_targets.py`, `autostart_config_to_targets.py`, `deploy_bundle_to_fleet.py`, `deploy_lab_defaults.sh`, `configure_auto_start.py`, `devices.csv` | Build offline wheels + debs, copy bundle, install on Pis, set up autostart services, and cleanly uninstall/reset. |
| Pi runtime processing     | `START_AUDIO_PROCESSING.sh`, `strip_monitor.py`, `audio_analysis_background.py`, `config_mic1.yaml`, `config_mic2.yaml`, `config_features.yaml`, `src/`                                                                                                                                          | Capture audio and run analysis pipeline on each Pi/mic process.                                                   |
| Control and communication | `speech_control.py`, `broadcast_ctrl.py`, `run_web.sh`, `receiver/`, `osc_collector.py`                                                                                                                                                                                                          | Start and control sessions, run GUI bridge, collect OSC streams.                                                  |
| Diagnostics and ops       | `diag_audio.py`, `gather_logs.sh`, `stop_two_mics.sh`, `check_deployment_status.py`                                                                                                                                                                                                              | Check health, inspect deploy phase status, gather logs, stop processes safely.                                    |

## Preferred Entrypoints

Use these first. Other scripts are secondary/manual tools.

| Task                            | Preferred command                                                               | Notes                                                       |
| ------------------------------- | ------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Build offline dependency wheels | `./prepare_wheelhouse.sh`                                                       | Run on the builder Pi (Phase 1).                            |
| Build offline system `.deb` set | `./prepare_debs.sh`                                                             | Run on the builder Pi (Phase 1). Ships in a separate patch. |
| Fleet deploy with lab defaults  | `bash deploy_lab_defaults.sh`                                                   | Convenience wrapper for lab defaults.                       |
| Fleet deploy, dry-run           | `bash deploy_lab_defaults.sh --dry-run`                                         | Virtual run of full deployment flow.                        |
| Copy bundle to targets          | `python3 copy_bundle_to_targets.py ...`                                         | Phase 2 only.                                               |
| Install bundle on targets       | `python3 install_bundle_on_targets.py ...`                                      | Phase 3 only.                                               |
| Configure autostart on targets  | `python3 autostart_config_to_targets.py ...`                                    | Phase 4 only.                                               |
| Check deploy phase status       | `python3 check_deployment_status.py ...`                                        | Read-only per-Pi status (SYNC/INSTALL/AUTOCFG/AUTORUN).     |
| Test expected recording rig     | `python speech_control.py test start_recording_session.yaml`                    | Exact-rig preflight before session start.                   |
| Start recording session         | `python speech_control.py start-recording-session start_recording_session.yaml` | Includes preflight test.                                    |
| Launch browser GUI              | `./run_web.sh --session start_recording_session.yaml`                           | Shows expected plus live process status.                    |
| Run Pi audio processing         | `./START_AUDIO_PROCESSING.sh`                                                   | Standard two-mic runtime on a Pi.                           |

## Secondary Utilities

These are useful but not primary entrypoints for normal operations:

- `setup_pi.sh`: lower-level environment setup script used by `install_from_bundle.sh` (internal; not a separate operator phase command).
- `deploy_bundle_to_fleet.py`: compatibility orchestrator for Phase 2+3 (supports combined and mode-flag workflows).
- `configure_auto_start.py`: lower-level autostart implementation used by `autostart_config_to_targets.py`.
- `uninstall_from_bundle.sh`: lower-level uninstall/reset helper for one target Pi.
- `broadcast_ctrl.py`: direct OSC broadcast utility for ad-hoc control.
- `audio_analysis_background.py`: lower-level launcher/helper path.
- `start_audio_server.sh`: one-process/manual server start workflow.

## Safety Rule For Future Refactors

If files are moved later, keep root-level wrapper scripts with the same names so old commands continue to work until all docs and operator habits are migrated.
