# Uninstall And Disk Footprint (Raspberry Pi)

This document explains exactly what gets installed on each Pi, where it lives, typical disk usage, and how to remove it.

Use this for:

- clean retests on one Pi
- rollback before rebuilding a golden bundle
- operator confidence about what changed on a target machine

## What Gets Installed

When you run [install_from_bundle.sh](../install_from_bundle.sh), it does two install layers.

1. System packages from local `debs/*.deb` via apt (offline, `--no-download`).
2. Python packages into a project venv at `PROJECT_DIR/venv` using `requirements-pi.txt`.

If autostart is later enabled via [configure_auto_start.py](../configure_auto_start.py), two user-level systemd services are also installed.

## Install Locations

Assuming `PROJECT_DIR=/home/admin/SPEECH_RECORD_ANALYSIS`:

- Project root: `/home/admin/SPEECH_RECORD_ANALYSIS`
- Python virtual environment: `/home/admin/SPEECH_RECORD_ANALYSIS/venv`
- Offline Python wheel cache: `/home/admin/SPEECH_RECORD_ANALYSIS/wheelhouse`
- Offline apt packages: `/home/admin/SPEECH_RECORD_ANALYSIS/debs`
- Models: `/home/admin/SPEECH_RECORD_ANALYSIS/models`
- Runtime logs/data (project-local):
  - `/home/admin/SPEECH_RECORD_ANALYSIS/log_data`
  - `/home/admin/SPEECH_RECORD_ANALYSIS/output`
  - `/home/admin/SPEECH_RECORD_ANALYSIS/logs`
- User services (if configured):
  - `~/.config/systemd/user/speech-record-mic1.service`
  - `~/.config/systemd/user/speech-record-mic2.service`

## Typical Disk Footprint

Observed in this project during bundle preparation:

- `wheelhouse/`: about 3.2 to 3.3 GB
- `models/`: about 1.1 GB
- `debs/`: about 27 to 28 MB

On each installed Pi, `venv/` can also become large (often multiple GB) because current Linux/aarch64 torch packaging may pull many `nvidia-*` wheels during install metadata resolution, even when GPU runtime is not used.

Quick check on a Pi:

```bash
cd /home/admin/SPEECH_RECORD_ANALYSIS
du -sh . venv wheelhouse models debs log_data output logs 2>/dev/null
```

## Uninstall Options

### Option A (Recommended for absolute clean state): reflash the Pi

For fleet-grade reproducibility, a fresh OS image is the only guaranteed zero-residue baseline.

### Option B: in-place uninstall/reset

Use [uninstall_from_bundle.sh](../uninstall_from_bundle.sh).

Default behavior (safe project cleanup):

- stops/disables `speech-record-mic1.service` and `speech-record-mic2.service` (if present)
- removes their unit files from `~/.config/systemd/user`
- kills running `strip_monitor.py` / `audio_analysis_background.py`
- removes project artifacts (`venv`, `wheelhouse`, `debs`, `models`, logs/output)
- keeps apt packages installed by default

Run:

```bash
cd /home/admin/SPEECH_RECORD_ANALYSIS
bash uninstall_from_bundle.sh
```

Full project removal:

```bash
cd /home/admin/SPEECH_RECORD_ANALYSIS
bash uninstall_from_bundle.sh --remove-project-dir
```

Deep cleanup (also purge apt packages listed in `requirements-apt.txt`):

```bash
cd /home/admin/SPEECH_RECORD_ANALYSIS
bash uninstall_from_bundle.sh --remove-project-dir --purge-apt --yes
```

## What Apt Purge Removes

The `--purge-apt` mode removes packages listed in [requirements-apt.txt](../requirements-apt.txt), currently:

- python3-venv
- python3-pip
- python3-dev
- portaudio19-dev
- libportaudio2
- libsndfile1
- ffmpeg
- git
- build-essential

Then it runs:

- `sudo apt-get autoremove --purge -y`
- `sudo apt-get clean`

Use apt purge only if this Pi is dedicated to this project or you understand the impact on other workloads.

## Post-Uninstall Verification

```bash
systemctl --user status speech-record-mic1.service speech-record-mic2.service || true
pgrep -af 'strip_monitor.py|audio_analysis_background.py' || true
test -d /home/admin/SPEECH_RECORD_ANALYSIS && du -sh /home/admin/SPEECH_RECORD_ANALYSIS || echo "project dir removed"
```
