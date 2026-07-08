# Main Deployment Workflow (Pi Fleet)

This is the end-to-end deployment workflow for the full Pi fleet.

For a complete script/domain overview, see [script_map.md](script_map.md).

It is designed for your current repo scripts:

- `prepare_wheelhouse.sh`
- `install_from_bundle.sh`
- `configure_auto_start.py`
- `devices.csv`

The flow has 4 phases:

1. Build the offline wheelhouse on one internet-connected Pi.
2. Bring that wheelhouse into the deployment bundle and copy the bundle to all Pis over SSH.
3. Run `install_from_bundle.sh` on all Pis (this creates `venv/` and installs dependencies).
4. Enable autostart services on all Pis.

## Preconditions

- All Pis are reachable by SSH on the same network.
- `devices.csv` contains the target list.
- The project is expected at `/home/pi/SPEECH_RECORD_ANALYSIS` on each Pi.
- `models/` is already present in the project folder (as required by `install_from_bundle.sh`).
- Recommended: SSH keys already set up from the deployment machine to each Pi.

---

## Phase 1: Build Wheelhouse On One Connected Pi

Use one Pi with internet access and the same OS/Python version as the offline Pis.

```bash
cd ~/SPEECH_RECORD_ANALYSIS
chmod +x prepare_wheelhouse.sh
./prepare_wheelhouse.sh
```

This creates:

- `wheelhouse/*.whl`

Quick check:

```bash
find wheelhouse -type f -name '*.whl' | head
```

If no wheels appear, stop and fix this first.

---

## Phase 2: Copy The Prepared Bundle To All Pis Over SSH

The bundle to distribute is the project folder including:

- code
- `models/`
- `wheelhouse/`

### 2A) Bring wheelhouse from the connected Pi into the deploy source folder

Run this on your deployment machine (replace source host if needed):

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS
rsync -az pi@192.168.0.11:/home/pi/SPEECH_RECORD_ANALYSIS/wheelhouse/ ./wheelhouse/
```

Now your local deploy source folder contains the same wheelhouse.

### 2B) Distribute the project folder to all Pis with rsync over SSH

Run this from the same deployment folder (where `devices.csv` is):

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS

TARGETS=$(awk -F, 'NR>1 {gsub(/ /,"",$3); gsub(/ /,"",$2); if($3!="") print $3; else print $2}' devices.csv)

for host in $TARGETS; do
  echo "==> Syncing to $host"
  rsync -az --delete \
    --exclude '.git/' \
    --exclude 'venv/' \
    --exclude '.wheelhouse-venv/' \
    --exclude '__pycache__/' \
    ./ pi@"$host":/home/pi/SPEECH_RECORD_ANALYSIS/
done
```

Notes:

- This replaces stale files on targets (`--delete`).
- It intentionally does not copy local `venv/`.
- If one Pi is your source/build Pi, syncing to it is still safe.

---

## Phase 3: Remote Install On All Pis (Create venv + Install)

`install_from_bundle.sh` already does the right thing for this workflow:

- validates `models/` and `wheelhouse/`
- checks required apt packages exist
- calls `setup_pi.sh` with `SKIP_APT=1`
- creates/uses `venv/`
- installs Python deps from local `wheelhouse/`

Run on all Pis from deployment machine:

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS

TARGETS=$(awk -F, 'NR>1 {gsub(/ /,"",$3); gsub(/ /,"",$2); if($3!="") print $3; else print $2}' devices.csv)

for host in $TARGETS; do
  echo "==> Installing on $host"
  ssh pi@"$host" 'cd /home/pi/SPEECH_RECORD_ANALYSIS && chmod +x *.sh && bash install_from_bundle.sh'
done
```

Optional parallel version (faster):

```bash
printf '%s\n' $TARGETS | xargs -n1 -P6 -I{} \
  ssh pi@{} 'cd /home/pi/SPEECH_RECORD_ANALYSIS && chmod +x *.sh && bash install_from_bundle.sh'
```

Quick remote check:

```bash
for host in $TARGETS; do
  ssh pi@"$host" 'test -x /home/pi/SPEECH_RECORD_ANALYSIS/venv/bin/python && echo "OK $(hostname)"'
done
```

### One-command shortcut for Phase 2 + 3

Use this helper to run wheelhouse pull (optional), sync, and remote install in one command:

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS
python3 deploy_bundle_to_fleet.py \
  --devices-file devices.csv \
  --devices 1-6 \
  --user pi \
  --pull-wheelhouse pi@192.168.0.11:/home/pi/SPEECH_RECORD_ANALYSIS/wheelhouse/
```

Dry-run first:

```bash
python3 deploy_bundle_to_fleet.py \
  --devices-file devices.csv \
  --devices 1-6 \
  --user pi \
  --pull-wheelhouse pi@192.168.0.11:/home/pi/SPEECH_RECORD_ANALYSIS/wheelhouse/ \
  --dry-run
```

If wheelhouse is already local, omit `--pull-wheelhouse`.

### One-command lab-default wrapper (Phase 2 + 3 + 4)

For daily lab use, run the wrapper script with built-in defaults:

- devices file: `devices.csv`
- devices: `1-6`
- user: `pi`
- wheelhouse source: `pi@192.168.0.11:/home/pi/SPEECH_RECORD_ANALYSIS/wheelhouse/`

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS
bash deploy_lab_defaults.sh
```

Dry-run (virtual run):

```bash
bash deploy_lab_defaults.sh --dry-run
```

Useful overrides:

```bash
bash deploy_lab_defaults.sh --skip-autostart
bash deploy_lab_defaults.sh --no-pull-wheelhouse
bash deploy_lab_defaults.sh --devices 1-3
```

---

## Phase 4: Enable Autostart Services On All Pis

Run from deployment machine in the repo root:

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS
python3 configure_auto_start.py --devices-file devices.csv --devices 1-6 --user pi
```

If needed (no SSH keys or sudo prompt expected):

```bash
python3 configure_auto_start.py --devices-file devices.csv --devices 1-6 --user pi --password 'YOUR_PI_PASSWORD'
```

The script configures and starts:

- `speech-record-mic1.service`
- `speech-record-mic2.service`

and prints a success/failure summary per device.

---

## Post-Deploy Validation

1. Verify service state from deployment machine:

```bash
TARGETS=$(awk -F, 'NR>1 {gsub(/ /,"",$3); gsub(/ /,"",$2); if($3!="") print $3; else print $2}' devices.csv)

for host in $TARGETS; do
  echo "==> $host"
  ssh pi@"$host" 'export XDG_RUNTIME_DIR="/run/user/$(id -u)"; export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"; systemctl --user is-active speech-record-mic1.service speech-record-mic2.service'
done
```

2. Verify recording workflow still uses session YAML (not devices.csv):

```bash
python speech_control.py test start_recording_session.yaml
```

That confirms the separation:

- `devices.csv`: install/autostart target inventory
- `start_recording_session.yaml`: recording-session target list

---

## Why This Split Is Correct

- Install/autostart should target the full hardware fleet (stable inventory).
- Recording sessions may use subsets (session-specific inventory).
- Keeping these as separate files prevents accidental under-deployment or wrong-session control.
