# Phase 3 — Fleet Deployment via SSH

Part of the [Fleet Deployment Guide](Fleet_Deployment_Guide.md). This document covers **Phase 3** only: rolling the validated bundle from the Mac out to every fleet Pi over SSH. Autostart is still not touched here — see [Phase 4](Autostart_Configuration_on_Fleet.md).

Do not run Phase 3 until [Phase 2](Test_Deployment_on_One_Pi.md) has passed on one Pi. If the bundle was broken, you catch it on one Pi; if you skip to Phase 3, you catch it on six.

## 1. Prerequisites

- [Phase 1](Bundle_Preparation_on_Builder_Pi.md) is complete. Mac has `wheelhouse/` and `debs/`.
- [Phase 2](Test_Deployment_on_One_Pi.md) succeeded on at least one Pi (typically `rpi5-11`). Audio and OSC verified.
- All six fleet Pis are powered up and reachable on the network defined in [../devices.csv](../devices.csv).
- Mac is on the same subnet.

Current lab targets (from [../devices.csv](../devices.csv)):

| Index | Hostname  | IP             | User |
| ----- | --------- | -------------- | ---- |
| 1     | `rpi5-11` | `192.168.0.11` | `pi` |
| 2     | `rpi5-12` | `192.168.0.12` | `pi` |
| 3     | `rpi5-13` | `192.168.0.13` | `pi` |
| 4     | `rpi5-14` | `192.168.0.14` | `pi` |
| 5     | `rpi5-15` | `192.168.0.15` | `pi` |
| 6     | `rpi5-16` | `192.168.0.16` | `pi` |

## 2. Reachability Sweep

Confirm every Pi answers before you start the deploy — a single unreachable Pi will make the batch report partial failure:

```bash
for ip in 192.168.0.11 192.168.0.12 192.168.0.13 192.168.0.14 192.168.0.15 192.168.0.16; do
    printf '%s ' "$ip"
    ping -c 1 -W 1 "$ip" >/dev/null && echo OK || echo DOWN
done
```

If any Pi shows `DOWN`, fix it before continuing. You can still deploy to a subset by passing `--devices 1,3,5-6` — but this is a workaround, not the intended path.

## 3. Set Up SSH Keys (Recommended)

The deploy script calls `rsync` and `ssh` several times per Pi. Without key-based auth you'll be typing the password (`1234`) many times, and long batches will pause on each prompt.

One-time setup, per Pi:

```bash
ssh-copy-id pi@192.168.0.11
ssh-copy-id pi@192.168.0.12
ssh-copy-id pi@192.168.0.13
ssh-copy-id pi@192.168.0.14
ssh-copy-id pi@192.168.0.15
ssh-copy-id pi@192.168.0.16
```

You will be prompted for the Pi password once per host, then never again from this Mac. Other machines still authenticate with the password as before — `ssh-copy-id` only appends your key, it does not disable password auth.

If you cannot use `ssh-copy-id`, you can install `sshpass` (`brew install hudochenkov/sshpass/sshpass` on macOS) and prefix commands with `SSHPASS=1234 sshpass -e ...`. Prefer the key method.

## 4. Deploy To The Whole Fleet

Two equivalent commands. Pick one.

### 4a. Direct call

```bash
./deploy_bundle_to_fleet.py --user pi --devices 1-6
```

### 4b. Lab-defaults wrapper (skip autostart on purpose)

```bash
./deploy_lab_defaults.sh --skip-autostart
```

The wrapper hard-codes the lab defaults (`--user pi`, `--devices 1-6`, wheelhouse source, etc.) so you don't have to remember them. `--skip-autostart` keeps Phase 4 out of this batch.

What happens for each Pi, in order:

1. `rsync -az --delete` bundle to `/home/pi/SPEECH_RECORD_ANALYSIS/` on the Pi.
2. Remote `install_from_bundle.sh`:
   - `apt install --no-download ./debs/*.deb` (offline, from bundle).
   - `setup_pi.sh` with `SKIP_APT=1` builds `venv/` and pip-installs everything from `./wheelhouse/`.

Expected end-of-run summary:

```
Summary:
  Sync ok: 1:192.168.0.11 2:192.168.0.12 3:192.168.0.13 4:192.168.0.14 5:192.168.0.15 6:192.168.0.16
  Sync failed: none
  Install ok: 1:192.168.0.11 2:192.168.0.12 3:192.168.0.13 4:192.168.0.14 5:192.168.0.15 6:192.168.0.16
  Install failed: none
```

Duration: on gigabit Ethernet with parallel `rsync` and warm caches, the whole fleet takes 5–15 minutes end to end. First-ever deploy is dominated by the ~3.5 GB wheelhouse transfer per Pi.

## 5. Post-Deploy Sanity On Each Pi

Deploy success from the Mac only proves rsync + installer both returned zero. The processes are **not** yet running. Verify a few Pis by hand before Phase 4:

```bash
# On the Mac
for i in 1 2 3 4 5 6; do
    ip="192.168.0.1$((i + 0))"        # 11..16
    echo "=== $ip ==="
    ssh pi@$ip 'test -x /home/pi/SPEECH_RECORD_ANALYSIS/venv/bin/python && echo venv ok; ls /home/pi/SPEECH_RECORD_ANALYSIS/models/iic/emotion2vec_plus_base/model.pt 2>/dev/null && echo model ok'
done
```

Every Pi should print `venv ok` and `model ok`. If one does not, deploy that one alone with `--devices <N>` and inspect the install output.

Optionally spot-check the mic pipeline on one Pi you did **not** already validate in Phase 2 — same commands as [Phase 2 § Run the mic pipeline manually](Test_Deployment_on_One_Pi.md#4-run-the-mic-pipeline-manually).

## 6. Handling Partial Failures

`deploy_bundle_to_fleet.py` reports which Pis succeeded and which failed separately, and continues past a single failure so one dead Pi does not block the other five. To re-target only the ones that failed:

```bash
./deploy_bundle_to_fleet.py --user pi --devices 2,5
```

For a `--no-delete` mode (skip `rsync --delete`, useful if a Pi has extra local data you must not wipe):

```bash
./deploy_bundle_to_fleet.py --user pi --devices 1-6 --no-delete
```

For a dry-run that prints the exact `rsync` and `ssh` commands without executing:

```bash
./deploy_bundle_to_fleet.py --user pi --devices 1-6 --dry-run
```

## 7. What Is Ready After Phase 3

- Every fleet Pi has code, models, wheelhouse, debs, and a populated `venv/`.
- The `.deb` packages are installed on the Pi's system Python and system libs — audio backends can open the USB mics.
- No mic process is running yet, and no systemd unit is registered yet.

To actually start recording, either:

- Run [../START_AUDIO_PROCESSING.sh](../START_AUDIO_PROCESSING.sh) manually per Pi (fine for ad-hoc tests), or
- Proceed to [Phase 4 — Autostart Configuration](Autostart_Configuration_on_Fleet.md) to have systemd own the processes across reboots.

Do not do both. Once autostart is enabled, running the launcher manually creates a duplicate set of processes.
