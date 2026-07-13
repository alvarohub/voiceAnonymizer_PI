# Quick Test Recipes: Laptop Only And Laptop + One Pi

This file is a focused runbook for the two practical test modes we just used:

- a local laptop sanity test
- a control-laptop plus one real Pi test

All commands below are run on the control computer, from the repository root,
unless a step is explicitly marked "On the Pi".

## 1. Recipe A: Laptop-Only Sanity Test

Use this when no Pi is available and you want to validate the GUI and OSC path.

1. Reset local processes.

```bash
./fresh_start_local.sh --dry-run
./fresh_start_local.sh
```

2. Start local test processing.

```bash
./START_LOCAL_TEST_PROCESSING.sh
```

3. Start the GUI bridge.

```bash
./run_web.sh --replace
```

Expected GUI behavior in this mode:

- `local-1` appears and should report `audio: ok`.
- `local-2` appears and should report `audio: failure` (intentional test behavior).

When done:

```bash
./fresh_start_local.sh --mics-only
./fresh_start_local.sh --bridge-only
```

## 2. Recipe B: Control Laptop + One Pi

Use this to test the full control path with one real Pi and no full six-Pi rig.

### 2.1 On the Pi (once per boot or after install)

Ensure microphone processes are running on that Pi.

```bash
cd /home/pi/SPEECH_RECORD_ANALYSIS
./START_AUDIO_PROCESSING.sh
```

If autostart services are installed and active, do not run the launcher on top of
existing services.

### 2.2 On the control laptop: create one-Pi session YAML

Create a small session file for one Pi so test/start commands check exactly that
machine.

```bash
cp start_recording_session.yaml start_recording_session_one_pi.yaml
```

Edit `start_recording_session_one_pi.yaml` so `pis:` contains only one entry:

```yaml
pis:
  - pi_id: rpi5-11
    ip: 192.168.0.11
    mics: [1, 2]
```

### 2.3 Start clean and launch GUI with expected list

```bash
./fresh_start_local.sh
./run_web.sh --replace --session start_recording_session_one_pi.yaml
```

Expected GUI behavior:

- only the chosen Pi/mic targets are marked as expected
- if the Pi is down, those expected rows stay missing/red
- no `local-*` rows should appear unless local test processes are running

### 2.4 Optional CLI control checks

```bash
python speech_control.py test start_recording_session_one_pi.yaml
python speech_control.py start-recording-session start_recording_session_one_pi.yaml
python speech_control.py broadcast --session start_recording_session_one_pi.yaml log_pause
python speech_control.py broadcast --session start_recording_session_one_pi.yaml log_resume
python speech_control.py broadcast --session start_recording_session_one_pi.yaml log_save_stop one_pi_session.csv
```

## 3. Troubleshooting Notes From This Test

### 3.1 "Why does local MacBook appear in the GUI?"

Because one or more local `strip_monitor.py` processes are still running.

Fix:

```bash
./fresh_start_local.sh --mics-only
```

### 3.2 "Why does GUI behavior look the same with and without --session?"

Because an older bridge process is still running, so new CLI options were not
applied to the process serving the browser.

Fix:

```bash
./run_web.sh --replace --session start_recording_session_one_pi.yaml
```

or reset bridge listeners first:

```bash
./fresh_start_local.sh --bridge-only
```

### 3.3 Quick port check

```bash
lsof -nP -iUDP:9000 -iTCP:8765 -iTCP:3000
```

The normal bridge is a `node` process listening on these ports.
