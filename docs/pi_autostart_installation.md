# Raspberry Pi Auto-Start Installation (2 mics x 6 Pis)

This guide installs two systemd user services per Pi so each device auto-starts
both microphone pipelines on boot.

Design follows the same service-manager pattern used in `configure_auto_start.py`
from the robot-game repository:

1. write user units
2. enable lingering
3. daemon-reload
4. enable and restart services

## 1. Rig mapping source of truth

For auto-start installation, the source of truth is now:

- devices.csv (repository root)

This is intentionally separate from start_recording_session.yaml.

- devices.csv = which Pis get systemd auto-start services installed
- start_recording_session.yaml = which Pi/mic processes are used for a specific recording session

IP mapping comes from:

- DISPLAY_BROADCAST_PROTOCOL.md -> section "4. Pi and player assignment"

Current mapping:

- rpi5-11 -> 192.168.0.11
- rpi5-12 -> 192.168.0.12
- rpi5-13 -> 192.168.0.13
- rpi5-14 -> 192.168.0.14
- rpi5-15 -> 192.168.0.15
- rpi5-16 -> 192.168.0.16

## 2. Preconditions on each Pi

Assume project path:

- /home/pi/SPEECH_RECORD_ANALYSIS

On each Pi:

```bash
cd /home/pi/SPEECH_RECORD_ANALYSIS
bash setup_pi.sh
```

Check config files exist:

- config_mic1.yaml
- config_mic2.yaml
- config_features.yaml

Important operational rule:

- Do not run START_AUDIO_PROCESSING.sh manually after enabling services.
- Use systemd start/stop/restart only, to avoid duplicate processes.

## 3. Create systemd user units

Preferred (scripted, colleague-style): run once from the control computer.

Quick start command:

```bash
python3 configure_auto_start.py
```

If SSH key login is not set up, pass username/password:

```bash
python3 configure_auto_start.py --user pi --password 'YOUR_PI_PASSWORD'
```

What this script does:

- reads the Pi list from devices.csv
- connects to each Pi
- writes one service file per mic under ~/.config/systemd/user/
- enables auto-start for those services
- restarts services so they are running immediately
- runs Pis in parallel (faster) and prints logs grouped per Pi
- prints a final summary: which Pis succeeded and which failed

Plain-language meaning of important options:

- --devices-file: which CSV file defines the Pi list (default: devices.csv)
- --devices: optional device indices, for example 1 2 3 or 1-6
- --user: Linux username on each Pi (default is pi)
- --password: Pi password used for SSH and for the one sudo step (only needed if key login/passwordless sudo are not set)
- --dry-run: print what would be executed, but do not change anything
- --skip-linger: skip the "enable linger" step (normally leave this off)

What "enable linger" means in practice:

- it allows user services to run at boot even before an interactive login
- this is what makes the recorder services come up automatically after reboot

Useful variants:

```bash
# Configure only selected devices by index
python3 configure_auto_start.py --devices 1 2

# Configure a range of devices
python3 configure_auto_start.py --devices 1-6

# Use an explicit devices file
python3 configure_auto_start.py --devices-file devices.csv

# See exactly what would happen without making changes
python3 configure_auto_start.py --dry-run
```

Manual equivalent is shown below.

On each Pi, create:

- ~/.config/systemd/user/speech-record-mic1.service
- ~/.config/systemd/user/speech-record-mic2.service

```bash
mkdir -p ~/.config/systemd/user
```

### 3.1 Unit: mic1

Create ~/.config/systemd/user/speech-record-mic1.service with:

```ini
[Unit]
Description=Speech Record Analysis MIC1
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/SPEECH_RECORD_ANALYSIS
ExecStart=/bin/bash -lc 'source /home/pi/SPEECH_RECORD_ANALYSIS/venv/bin/activate && python3 -u strip_monitor.py --config config_mic1.yaml --features-config config_features.yaml'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

### 3.2 Unit: mic2

Create ~/.config/systemd/user/speech-record-mic2.service with:

```ini
[Unit]
Description=Speech Record Analysis MIC2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/pi/SPEECH_RECORD_ANALYSIS
ExecStart=/bin/bash -lc 'source /home/pi/SPEECH_RECORD_ANALYSIS/venv/bin/activate && python3 -u strip_monitor.py --config config_mic2.yaml --features-config config_features.yaml'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
```

## 4. Enable user manager at boot and activate services

On each Pi:

```bash
sudo loginctl enable-linger pi
systemctl --user daemon-reload
systemctl --user enable speech-record-mic1.service speech-record-mic2.service
systemctl --user restart speech-record-mic1.service speech-record-mic2.service
```

## 5. Verify

```bash
systemctl --user status speech-record-mic1.service --no-pager
systemctl --user status speech-record-mic2.service --no-pager
journalctl --user -u speech-record-mic1.service -n 80 --no-pager
journalctl --user -u speech-record-mic2.service -n 80 --no-pager
```

From the control computer (in this repository):

Important: this still uses start_recording_session.yaml (recording workflow), not devices.csv.

```bash
python speech_control.py test start_recording_session.yaml
```

That test must report all 12 expected processes as healthy.

## 6. Fleet rollout helper (from control computer)

If SSH keys are ready, run once per Pi:

```bash
for host in 192.168.0.11 192.168.0.12 192.168.0.13 192.168.0.14 192.168.0.15 192.168.0.16; do
  echo "=== $host ==="
  ssh pi@$host 'cd /home/pi/SPEECH_RECORD_ANALYSIS && mkdir -p ~/.config/systemd/user'
done
```

Then copy unit files and activate using the commands in sections 3 and 4.

## 7. Safe day-of-demo operations

- Start/restart all Pi processes: systemctl --user restart on each Pi.
- Stop all Pi processes: systemctl --user stop on each Pi.
- Run exact-rig preflight from control machine before recording.

Suggested command sequence at session start:

```bash
python speech_control.py test start_recording_session.yaml
python speech_control.py start-recording-session start_recording_session.yaml
```

## 8. Troubleshooting

If a service fails repeatedly:

1. check journalctl output
2. verify venv exists at /home/pi/SPEECH_RECORD_ANALYSIS/venv
3. verify config_mic1.yaml and config_mic2.yaml have correct audio_device and osc_ip
4. confirm no leftover manual process is already binding the same resources
5. run a manual one-shot command from the same directory to isolate config issues

Manual one-shot checks:

```bash
cd /home/pi/SPEECH_RECORD_ANALYSIS
source venv/bin/activate
python3 strip_monitor.py --config config_mic1.yaml --features-config config_features.yaml
python3 strip_monitor.py --config config_mic2.yaml --features-config config_features.yaml
```
