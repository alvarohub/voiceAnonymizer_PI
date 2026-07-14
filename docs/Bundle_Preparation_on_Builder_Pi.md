# Phase 1 — Bundle Preparation on the Builder Pi

Part of the [Fleet Deployment Guide](Fleet_Deployment_Guide.md). This document covers **Phase 1** only: producing a complete offline deployment bundle (`wheelhouse/` + `debs/`) on the internet-connected builder Pi and pulling it to the Mac.

## 0. What You Need Before Starting

- The Mac (control machine) is on the same local network as the builder Pi and can `ssh admin@192.168.1.48` (the builder Pi, hostname `emotionpi`).
- The builder Pi has real internet access — verify with `ping -c 3 8.8.8.8` on the Pi.
- The builder Pi runs the same Raspberry Pi OS release and CPU architecture as the fleet Pis (Raspberry Pi OS 64-bit / aarch64). Same Python version too. If any of these differ, the wheels or `.deb`s may refuse to install on the fleet.

If the SSH user/IP is different on your side, adjust the commands. The examples below use the current lab values.

## 1. Sync The Repo To The Builder Pi

The builder Pi runs the two preparation scripts, so it needs the current repo. Push the code from the Mac:

```bash
# On the Mac, from the repo root.
rsync -avz --exclude '.git/' --exclude 'venv/' --exclude '.wheelhouse-venv/' \
    ./ admin@192.168.1.48:/home/admin/SPEECH_RECORD_ANALYSIS/
```

(You can also `git clone` on the Pi if you prefer; both work.)

## 2. Build The Python Wheelhouse

On the builder Pi:

```bash
ssh admin@192.168.1.48
cd /home/admin/SPEECH_RECORD_ANALYSIS
./prepare_wheelhouse.sh
```

What this does:

- Creates a throw-away venv `.wheelhouse-venv/`.
- Reads [../requirements-pi.txt](../requirements-pi.txt).
- Downloads every wheel + all transitive dependencies (including the big ones — `torch` is ~430 MB) into `./wheelhouse/`.
- Includes `pip`, `wheel`, `setuptools` so the fleet installer can bootstrap without touching PyPI.

Expected duration: 10–30 minutes on the first run (network-bound). Re-runs are near-instant because pip caches.

Verify at the end:

```bash
ls wheelhouse | wc -l          # should print ~130-150 files
find wheelhouse -name 'torch-*.whl'   # confirms torch landed
```

### 2.1 If a wheel keeps failing with `IncompleteRead`

That is a PyPI network drop, not a code issue. The download resumes on re-run, but for a stubborn one:

- Add `--default-timeout=1000` to the pip call, or
- Download that one wheel on the Mac (`pip download <pkg> --no-deps -d /tmp`) and `scp` it into `wheelhouse/` on the Pi, then re-run the script. It will detect the pre-placed file and skip.

## 3. What Debs Are Shipped

The fleet Pis need the same OS-level libraries that `sounddevice`, `torchaudio`, `soundfile`, and `ffmpeg` link against. `pip` cannot install these — they are `.deb` packages. The current list, matched to what [../install_from_bundle.sh](../install_from_bundle.sh) requires on the fleet, is:

- `python3-venv`, `python3-pip`, `python3-dev`
- `portaudio19-dev`, `libportaudio2`
- `libsndfile1`
- `ffmpeg`
- `git`, `build-essential`

`prepare_debs.sh` downloads all of them **plus every transitive dependency** into `./debs/` on the builder Pi, without installing them (they are already installed on the builder — we just want the `.deb` files themselves for shipping).

## 4. Download The Debs

On the builder Pi:

```bash
cd /home/admin/SPEECH_RECORD_ANALYSIS
./prepare_debs.sh
```

Verify:

```bash
ls debs | grep -E 'portaudio|sndfile|ffmpeg' | head
ls debs/*.deb | wc -l          # roughly 30-60 files depending on the OS release
```

> **If `prepare_debs.sh` is not yet in your checkout** (it lands in a separate patch after this doc), the manual equivalent is:
>
> ```bash
> cd /home/admin/SPEECH_RECORD_ANALYSIS
> mkdir -p debs && sudo rm -f debs/*.deb
> sudo apt-get update
> sudo apt-get install -y --reinstall --download-only \
>     -o Dir::Cache::archives="$PWD/debs" \
>     python3-venv python3-pip python3-dev \
>     portaudio19-dev libportaudio2 \
>     libsndfile1 ffmpeg git build-essential
> sudo chown -R "$USER" debs
> ```
>
> This is exactly what the script will do, wrapped for idempotency and safety.

## 5. Pull The Bundle Parts Back To The Mac

On the Mac:

```bash
cd /path/to/SPEECH_RECORD_ANALYSIS
rsync -avz admin@192.168.1.48:/home/admin/SPEECH_RECORD_ANALYSIS/wheelhouse/ ./wheelhouse/
rsync -avz admin@192.168.1.48:/home/admin/SPEECH_RECORD_ANALYSIS/debs/       ./debs/
```

The wheelhouse is ~3.5 GB; on gigabit Ethernet the pull takes a few minutes.

## 6. Final Bundle Layout On The Mac

At this point your Mac's repo root should contain everything a fleet Pi needs — no internet required from here on:

```
SPEECH_RECORD_ANALYSIS/
├── src/                       # code
├── models/                    # emotion2vec + silero-vad (see models/README.md)
├── wheelhouse/                # ~130 .whl files, ~3.5 GB
├── debs/                      # ~30-60 .deb files
├── requirements-pi.txt
├── install_from_bundle.sh
├── setup_pi.sh
├── deploy_bundle_to_fleet.py
├── devices.csv
└── ... (configs, launchers, ...)
```

Sanity check on the Mac before moving to Phase 2:

```bash
ls wheelhouse/*.whl | wc -l    # non-zero
ls debs/*.deb      | wc -l     # non-zero
ls models/iic/emotion2vec_plus_base/model.pt   # exists
ls models/silero-vad/hubconf.py                # exists
```

If all four pass, the bundle is complete. Unplug the builder Pi and continue with [Phase 2 — Test Deployment on One Pi](Test_Deployment_on_One_Pi.md).

## 7. Notes And Gotchas

- **The `wheelhouse/` and `debs/` folders are gitignored.** They are large, architecture-specific, and rebuilt any time a new dependency is added.
- **Do not commit them to Git.** Even the Mac copy should be rebuilt via this Phase 1 whenever `requirements-pi.txt` or the apt package list changes.
- **Same-arch requirement.** These wheels and `.deb`s are `aarch64`-only. They will not install on an `x86_64` Pi image (Pi 4/5 default 64-bit OS is fine).
- **The builder Pi does not need to be part of the fleet.** In our lab it is a spare 16 GB Pi 5 used only for bundle work.
