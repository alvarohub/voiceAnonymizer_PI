# Data Collection and CSV Format

Authoritative reference for how session data is logged in RAM, saved to disk on the Pis, collected to the Mac, and interpreted downstream. Part of the runtime workflow — for the session-level operator commands, see [Runtime_Operation.md](Runtime_Operation.md).

## 1. Overview

Each mic process on each Pi runs a small in-RAM buffer. When you tell it to log, it accumulates rows; when you tell it to save, it writes CSV files. When you tell it to discard, the buffer is cleared without writing.

Files are always written on the Pi's local disk under the process's configured `output_dir` (usually `log_data/`). To centralize them on the Mac after a session, use `save_and_pull_logs.py`, which pairs saving and pulling into one atomic operation.

Three file kinds are produced per mic process per save:

- `*_opensmile_lld.csv` — openSMILE low-level descriptors (per-frame features).
- `*_vad.csv` — voice activity detection timeline.
- `*_emotion.csv` — emotion inference outputs (only if `emotion_on` was enabled for the session).

All three share a common leading timing prefix so they can be re-aligned per row: `name`, `frameTime`, `unix_start`, `unix_end`.

## 2. Session Lifecycle

The lifecycle is intentionally two-phased: "start logging" fills a RAM buffer, "save" flushes to disk. This lets you decide after the fact whether a run is worth keeping.

### 2.1 Start logging

```bash
python speech_control.py broadcast --session start_recording_session.yaml log_start 60
```

- `60` = `max_minutes` cap. Default is 60 if omitted.
- On reaching the cap, new rows stop being appended to RAM, but the session stays open. You can still save or discard.
- The cap exists to keep memory bounded during long unattended runs.

### 2.2 Save (with pull to Mac in one step)

Preferred:

```bash
python save_and_pull_logs.py --session start_recording_session.yaml take_001
```

What this script does:

1. Reads the expected process list from the session YAML.
2. Sends `log_save_stop take_001` to every expected process.
3. Waits for save acknowledgments and per-file `saved` notices.
4. `scp`s each reported file path from the Pi to the Mac.

Files land on the Mac at:

```
log_data/pulled/<YYYYMMDD-HHMMSS>/<pi-id>/<base>_<pi-id>-<mic-id>_<kind>.csv
```

Useful options: `--ssh-user`, `--dest-dir`, `--ack-timeout`, `--scp-timeout`, `--dry-run`.

### 2.3 Save (Pi-local only, no pull)

If you want to save on the Pis but pull later manually:

```bash
python speech_control.py broadcast --session start_recording_session.yaml log_save_stop take_001
```

Files remain on each Pi under `log_data/` until you copy them back with `scp` / `rsync`.

### 2.4 Discard

```bash
python speech_control.py broadcast --session start_recording_session.yaml log_discard_stop
```

Clears the RAM buffer on every process without writing to disk. Use when a run is spoiled and not worth keeping.

## 3. Filename Rules

If you pass a base name (e.g. `take_001`), the process derives:

```
<base>_<pi-id>-<mic-id>_<kind>.csv
```

Concrete example for one save across two Pis (2 mics each):

```
take_001_rpi5-11-1_opensmile_lld.csv
take_001_rpi5-11-1_vad.csv
take_001_rpi5-11-1_emotion.csv
take_001_rpi5-11-2_opensmile_lld.csv
take_001_rpi5-11-2_vad.csv
take_001_rpi5-11-2_emotion.csv
take_001_rpi5-12-1_opensmile_lld.csv
... etc.
```

If no base name is provided, the process auto-generates one from `pi_id`, `mic_id`, current date/time, and duration in seconds, so nothing ever gets overwritten silently.

## 4. Save Confirmations

For each save command, every process replies with:

1. A single `save` acknowledgment (per process).
2. One `saved` notice per file it wrote. Each notice includes:
   - `kind` (`opensmile_lld` / `vad` / `emotion`)
   - `filename`
   - `full path on the Pi`

`save_and_pull_logs.py` uses those exact paths for the SCP pull step — no path guessing.

## 5. CSV Schema

### 5.1 Common leading columns (all three file kinds)

| Column       | Type                       | Meaning                                                                                                 |
| ------------ | -------------------------- | ------------------------------------------------------------------------------------------------------- |
| `name`       | string                     | Process identity, typically `<pi-id>-<mic-id>` (e.g. `rpi5-11-1`). Same value in every row of one file. |
| `frameTime`  | float, seconds             | Time since the process started (monotonic; not wall clock). Useful for intra-process comparison.        |
| `unix_start` | float, seconds since epoch | Wall-clock start of the frame. Use this for cross-Pi alignment.                                         |
| `unix_end`   | float, seconds since epoch | Wall-clock end of the frame. `unix_end - unix_start` = frame duration.                                  |

For cross-mic and cross-Pi alignment, always use `unix_start` / `unix_end`. `frameTime` resets on each process restart.

### 5.2 `*_opensmile_lld.csv`

Common columns above, then the openSMILE low-level descriptor columns selected in [../config_features.yaml](../config_features.yaml) (`opensmile.lld_columns`).

Typical selection is a subset of the eGeMAPS LLDs (F0, jitter, shimmer, loudness, HNR, MFCC 1–4, ...). The exact column set is whatever the config asked for at run time; the header row is the source of truth.

Reference for the openSMILE feature set and column meanings: [openSmile_information.md](openSmile_information.md).

### 5.3 `*_vad.csv`

Common columns, then:

| Column | Meaning                                            |
| ------ | -------------------------------------------------- |
| `vad`  | Voice activity flag (0 or 1). One value per frame. |

Cadence matches the VAD frame rate configured on the Pi (usually 25–50 Hz).

### 5.4 `*_emotion.csv`

Common columns, then:

| Column            | Meaning                                                                                                                                                |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `voiced_fraction` | Fraction of the emotion window that VAD marked as voiced. Rows with very low voiced fraction are usually noise-only and should be filtered downstream. |
| `label`           | Top-1 emotion label predicted for the window.                                                                                                          |
| `confidence`      | Softmax confidence for `label`.                                                                                                                        |
| `emotion_<name>`  | One column per emotion class in the model's label set, with the softmax score.                                                                         |

Emotion files exist only for sessions where `emotion_on` was sent (via `start-recording-session`, if the session YAML enables it). The exact label set depends on the model chosen (currently `iic/emotion2vec_plus_base`).

## 6. Failure Handling

- If a process does not acknowledge `save` in time, `save_and_pull_logs.py` reports per-process timeout and moves on to the next.
- If an SCP pull fails, it reports that specific file and continues; other files still land.
- Save + pull are separated by explicit acknowledgments, so each failure is observable and re-runnable per file.
- If a Pi crashed mid-session and never replied to `log_start`, its data was never buffered — nothing to save. Preflight (`speech_control.py test ...`) is designed to catch this before you start recording.

## 7. Session YAML Recap

The relevant section of [../start_recording_session.yaml](../start_recording_session.yaml):

```yaml
logging:
  start: true # if true, log_start is sent at session start
  max_minutes: 60 # cap on in-RAM recording length
```

Full YAML shape reference: [operator_osc_control.md § The Session YAML](operator_osc_control.md#the-session-yaml).

## 8. Related Docs

- [Runtime_Operation.md](Runtime_Operation.md) — how sessions are started, paused, and stopped from the Mac.
- [operator_osc_control.md](operator_osc_control.md) — every OSC command and the full session YAML.
- [openSmile_information.md](openSmile_information.md) — column-by-column reference for the openSMILE LLD file.
- [pi_runtime_processing.md](pi_runtime_processing.md) — where `output_dir` is configured per Pi.
