# Phase 4 — Autostart Configuration on the Fleet

Part of the [Fleet Deployment Guide](Fleet_Deployment_Guide.md). This document covers **Phase 4** only: installing systemd **user** services on every fleet Pi so both microphone processes launch automatically at boot and are supervised (restart on crash).

> **Do not run Phase 4 until Phases 1–3 pass on every Pi.** Once autostart is enabled, systemd owns the mic processes. Running `START_AUDIO_PROCESSING.sh` manually on top of a running service creates duplicate processes that fight over the same USB microphone and the same OSC ports.

## 1. What Gets Installed

Two systemd **user** unit files per Pi, both under `~/.config/systemd/user/`:

- `speech-record-mic1.service` → runs `strip_monitor.py --config config_mic1.yaml --features-config config_features.yaml`
- `speech-record-mic2.service` → runs `strip_monitor.py --config config_mic2.yaml --features-config config_features.yaml`

Both units use the same shape:

```ini
[Unit]
Description=Speech Record Analysis MIC{N}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/SPEECH_RECORD_ANALYSIS
ExecStart=/bin/bash -lc 'source venv/bin/activate && python3 -u strip_monitor.py --config config_mic{N}.yaml --features-config config_features.yaml'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

Systemd will restart the process automatically if it crashes (`Restart=always`, 2-second backoff).

`loginctl enable-linger pi` is also invoked once per Pi so the user's systemd instance stays alive across reboots even before someone logs in — otherwise user services would only start at first interactive login.

## 2. Run The Installer

From the Mac, from the repo root:

```bash
python3 configure_auto_start.py --user pi
```

What it does, per Pi in [../devices.csv](../devices.csv):

1. SSH in and write both unit files to `~/.config/systemd/user/`.
2. `sudo loginctl enable-linger pi` (needs the Pi's sudo password once — see `--password` below).
3. `systemctl --user daemon-reload`.
4. `systemctl --user enable speech-record-mic1.service speech-record-mic2.service`.
5. `systemctl --user restart speech-record-mic1.service speech-record-mic2.service`.

Pis are processed in parallel, and the final line prints per-Pi success/failure.

### Useful variants

```bash
# One Pi only (typical: retry a failed one)
python3 configure_auto_start.py --user pi --devices 3

# Subset
python3 configure_auto_start.py --user pi --devices 1-3

# Dry-run: print SSH commands, do not touch anything
python3 configure_auto_start.py --user pi --dry-run

# Non-interactive password (needed if key-based auth is set but sudo still asks for a password)
python3 configure_auto_start.py --user pi --password '1234'
```

If SSH keys are set up ([Fleet_Deployment_via_SSH.md § 3](Fleet_Deployment_via_SSH.md#3-set-up-ssh-keys-recommended)) and sudo does not require a password on the Pi, `--password` is not needed.

## 3. Reboot Test

The whole point of Phase 4 is that autostart survives reboot. Verify by rebooting one Pi and checking:

```bash
# From the Mac
ssh pi@192.168.0.11 'sudo reboot'
# wait ~60 seconds for the Pi to come back
ping -c 3 192.168.0.11
```

Then, on the Mac, launch the receiver ([../run_web.sh](../run_web.sh)):

```bash
./run_web.sh --session start_recording_session.yaml
```

Within a few seconds after the Pi has booted, `rpi5-11` should appear in the browser GUI with `mic 1` and `mic 2` reporting `audio: ok`, without anyone SSHing into the Pi. If that works on one Pi, do the same reboot test on the remaining five.

## 4. Verifying And Debugging

### 4.1 Per-Pi status

SSH into a Pi and check the services:

```bash
ssh pi@192.168.0.11
systemctl --user status speech-record-mic1.service --no-pager
systemctl --user status speech-record-mic2.service --no-pager
```

`active (running)` is what you want. `failed` or `activating (auto-restart)` means the process keeps crashing.

### 4.2 Logs

Recent log output for a mic service:

```bash
journalctl --user -u speech-record-mic1.service -n 200 --no-pager
```

Follow live:

```bash
journalctl --user -u speech-record-mic1.service -f
```

Common issues:

- **`audio_device` not found** — the USB mic naming differs from the config. Fix [../config_mic1.yaml](../config_mic1.yaml) / [../config_mic2.yaml](../config_mic2.yaml) and `systemctl --user restart speech-record-mic{1,2}.service`.
- **`torch` model file missing** — Phase 1 bundle was incomplete. Re-check `models/iic/emotion2vec_plus_base/model.pt` on the Pi.
- **Service starts before network up** — extremely rare with `Wants=network-online.target`, but if it happens increase the delay or add `Restart=on-failure` with a bigger `RestartSec`.

### 4.3 Manual start/stop/restart

Once autostart is on, use systemd, not the launcher:

```bash
systemctl --user restart speech-record-mic1.service
systemctl --user stop    speech-record-mic1.service speech-record-mic2.service
systemctl --user start   speech-record-mic1.service speech-record-mic2.service
```

## 5. Removing Autostart (If Ever Needed)

To roll back one Pi to manual-only:

```bash
ssh pi@192.168.0.11
systemctl --user stop    speech-record-mic1.service speech-record-mic2.service
systemctl --user disable speech-record-mic1.service speech-record-mic2.service
rm ~/.config/systemd/user/speech-record-mic*.service
systemctl --user daemon-reload
sudo loginctl disable-linger pi        # only if no other user service needs linger
```

After this the Pi behaves like the end of Phase 3 — deployed but idle, ready for manual `./START_AUDIO_PROCESSING.sh`.

## 6. Interaction With Recording Sessions

Autostart runs the mic capture pipeline continuously. It does **not** start a "recording session" — that is a separate operator command (`speech_control.py start-recording-session ...`) which flips per-Pi flags to enable feature CSV writing. The autostart layer just keeps the plumbing warm.

Full operator workflow after Phase 4: [operator_osc_control.md](operator_osc_control.md).
Runtime config reference: [pi_runtime_processing.md](pi_runtime_processing.md).

## 7. Sanity: What The Fleet Looks Like After Phase 4

- Every Pi boots with two mic services running, restarted automatically on crash.
- Manually starting `START_AUDIO_PROCESSING.sh` is now the wrong move — it produces duplicate processes.
- The operator drives everything from the Mac (`speech_control.py`, `run_web.sh`).
- If a Pi is rebooted, mic services come back within ~60 seconds without human action.

At this point deployment is complete.
