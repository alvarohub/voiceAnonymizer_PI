# Central Collection, CSV Logs, And Data Meaning

This document explains what the central computer receives, what gets written to CSV, how timestamps work, what the values mean, and how the processing switches affect the data.

## Table Of Contents

- [Central Collection, CSV Logs, And Data Meaning](#central-collection-csv-logs-and-data-meaning)
  - [Table Of Contents](#table-of-contents)
  - [1. Quick Summary](#1-quick-summary)
  - [2. System Roles](#2-system-roles)
  - [3. Discovery And OSC Routing](#3-discovery-and-osc-routing)
    - [3.1 Browser Receiver Screenshots](#31-browser-receiver-screenshots)
  - [4. CSV Outputs](#4-csv-outputs)
    - [4.1 Central OSC Collector CSV](#41-central-osc-collector-csv)
    - [4.2 Pi-Local Research CSVs](#42-pi-local-research-csvs)
    - [4.3 Which CSV Should I Use?](#43-which-csv-should-i-use)
  - [5. Retrieving Pi-Local Logs](#5-retrieving-pi-local-logs)
    - [5.1 Network Copy](#51-network-copy)
    - [5.2 Physical SD-Card Copy](#52-physical-sd-card-copy)
  - [6. Timing, Sampling, And Model Rates](#6-timing-sampling-and-model-rates)
  - [7. Data Values And Ranges](#7-data-values-and-ranges)
    - [7.1 VAD](#71-vad)
    - [7.2 Prosody And openSMILE](#72-prosody-and-opensmile)
    - [7.3 Emotion](#73-emotion)
    - [7.4 Self Telemetry](#74-self-telemetry)
  - [8. Runtime Switches](#8-runtime-switches)
  - [9. Why VAD Matters](#9-why-vad-matters)
  - [10. Extending The Collected Features](#10-extending-the-collected-features)

## 1. Quick Summary

There are two different CSV capture paths, and the Pi-local path now writes separate research files per analysis stream:

| CSV path                  | Where it runs     | Best for                                                           |
| ------------------------- | ----------------- | ------------------------------------------------------------------ |
| Central OSC collector CSV | central computer  | raw archive of every OSC packet from many Pis and microphones      |
| Pi-local research CSVs    | each Raspberry Pi | sample-indexed openSMILE, VAD, and emotion logs for one microphone |
| Pi-local combined CSV     | each Raspberry Pi | compatibility/status table at the lower live logger rate           |

The central computer does not open microphones and does not run VAD, openSMILE, or emotion2vec during a real multi-Pi session. Those models run on each Pi. The central computer receives OSC messages, displays them in the browser, sends control commands, and can archive the OSC stream.

For a laptop dry run with no Pi available, the central computer can intentionally run two local `strip_monitor.py` processes via `./START_LOCAL_TEST_PROCESSING.sh`: `local-1` uses the system default microphone, and `local-2` intentionally reports `audio: failure` because `MIC2` is absent. This tests the same receiver device menu and failure signaling path without changing the real `MIC1`/`MIC2` Pi configs.

For real sessions, VAD should normally be active. Without VAD, the system treats the gate as open and may process silence, room noise, handling sounds, or electrical noise as if it were speech.

## 2. System Roles

Each Raspberry Pi runs one `strip_monitor.py` process per microphone. That Pi process is responsible for:

- opening the local Linux audio input named in `config_mic1.yaml` or `config_mic2.yaml`
- resampling microphone audio to the project sample rate, normally 16 kHz
- running VAD, openSMILE prosody, and emotion inference when those stages are enabled
- sending OSC telemetry to the central computer
- optionally writing Pi-local CSV logs under `log_data/`

The central computer can run:

- `receiver/bridge.js`, launched by `./run_web.sh`, for the browser GUI and remote control buttons
- `osc_collector.py`, for central CSV capture of the OSC stream

The central computer identifies streams by logical `device_id`, not by Linux microphone names. A device id is built on the Pi as `<pi_id>-<mic_id>`, for example `5-1` or `5-2`.

## 3. Discovery And OSC Routing

Every Pi process periodically broadcasts:

```text
/hello <device_id> <pi_id> <mic_id> <hostname> <ctrl_port> <version>
```

This lets the central bridge discover Pis and route control commands back to the correct process.

Analysis data is namespaced under:

```text
/dev/<device_id>/...
```

For example:

```text
/dev/5-1/speech/vad
/dev/5-1/speech/F0semitoneFrom27.5Hz_sma3nz
/dev/5-1/speech/emo/label
/dev/5-1/stats/self
```

This namespace is what lets many Pis and many microphones share one central OSC port.

Control commands have an application-level acknowledgement. When the browser bridge sends a `/ctrl/...` command, it appends a command id and ACK port. The Pi executes the command and replies with:

```text
/dev/<device_id>/ack <command> <cmd_id> <ok> <message>
```

The bridge and `broadcast_ctrl.py` wait `150` ms by default. On a wired Ethernet network this is still much longer than a normal round trip, but short enough that the operator sees a warning quickly if a command packet or reply is lost. Set `ACK_TIMEOUT_MS` before starting the bridge, or pass `--ack-timeout-ms` to `broadcast_ctrl.py`, if a different threshold is needed.

This ACK layer applies only to control commands. Live telemetry packets such as VAD, prosody, emotion, and self-telemetry remain best-effort UDP streams.

### 3.1 Browser Receiver Screenshots

The browser receiver is the central visual control surface. These screenshots use simulated OSC data, but the layout is the same when real Pis are broadcasting.

![Central receiver live view](images/central-receiver-live.png)

![Central receiver compact view](images/central-receiver-compact.png)

## 4. CSV Outputs

### 4.1 Central OSC Collector CSV

Run the central collector on the central computer:

```bash
source venv/bin/activate
python osc_collector.py --bind 0.0.0.0 --port 9000 --out log_data/multi
```

`osc_collector.py` writes one CSV file per `device_id`, opened when the first `/dev/<device_id>/...` packet arrives:

```text
log_data/multi/<YYYYMMDD_HHMMSS>_<device_id>.csv
```

The central collector CSV uses a long schema: one row per OSC message.

| Column         | Meaning                                                               |
| -------------- | --------------------------------------------------------------------- |
| `recv_ts_iso`  | UTC receive time on the central computer, ISO formatted.              |
| `recv_ts_unix` | UTC receive time on the central computer, Unix seconds with decimals. |
| `sender_ip`    | IP address that sent the OSC packet.                                  |
| `device_id`    | Logical stream id, for example `5-1`.                                 |
| `address`      | Full OSC address, for example `/dev/5-1/speech/vad`.                  |
| `args_json`    | OSC arguments serialized as JSON.                                     |

This format deliberately does not create one column per feature. It accepts any current or future OSC topic without changing the schema. For analysis, pivot this long table into a wide table later if needed.

Important timestamp detail: `recv_ts_iso` and `recv_ts_unix` are central-computer receive times, not the original audio sample times on the Pi. They are good for ordering packets and approximate wall-clock alignment, but they include network and scheduling jitter.

### 4.2 Pi-Local Research CSVs

When logging is enabled from the browser or from `/ctrl/log_start`, the Pi starts a RAM-backed log session. During the session it marks the start time and appends sample-indexed rows in memory. No CSV files are created while the session is recording.

When the operator presses **SAVE LOG** or sends `/ctrl/log_save_stop`, the Pi writes the enabled CSV outputs under the configured `output_dir`:

```text
log_data/<chosen_name>.csv
log_data/<chosen_name>_opensmile_lld.csv
log_data/<chosen_name>_vad.csv
log_data/<chosen_name>_emotion.csv
```

If the operator presses **DISCARD** or sends `/ctrl/log_discard_stop`, the RAM session is cleared and no files are written. This means there is no temporary CSV cleanup step in normal operation.

Because the active session is stored in RAM until save, an unsaved session is lost if the Pi process exits or the Pi loses power before **SAVE LOG** is pressed. For long unattended runs, save at natural experiment boundaries rather than keeping one very long unsaved session open.

The suffixes and which files are enabled are controlled by `config_features.yaml`, not by the central browser. The browser and OSC controls can start/stop logging and toggle stages, but the experiment file decides the computed feature set and CSV shape.

The research alignment key is the sample timeline after resampling to the project rate, normally 16 kHz. The important columns are:

| Column                  | Meaning                                                                                   |
| ----------------------- | ----------------------------------------------------------------------------------------- |
| `device_id`             | Logical stream id, for example `5-1`.                                                     |
| `sample_rate`           | Analysis sample rate, normally `16000`.                                                   |
| `sample_start`          | First target-rate sample covered by this row.                                             |
| `sample_end`            | End sample for this row. Treat it as exclusive for interval math.                         |
| `sample_center`         | Center sample of the frame/window.                                                        |
| `start_s`, `end_s`      | `sample_start / sample_rate` and `sample_end / sample_rate`.                              |
| `time_s`                | `sample_center / sample_rate`.                                                            |
| `timestamp_unix_ms_est` | Estimated wall-clock time for the center sample, useful metadata but not the primary key. |
| `session_start_unix_ms` | Unix timestamp in milliseconds for the logging session start. Repeated on every row.      |
| `session_start_iso`     | ISO timestamp for the logging session start. Repeated on every row.                       |

The openSMILE research file has one row per openSMILE low-level descriptor frame. It includes:

- the timing/sample columns above
- `frame_index`, derived from the VAD/openSMILE 100 Hz analysis grid
- `vad`, the tri-state VAD value at the frame center
- `audio_overflow_count`, the cumulative number of audio callback overflow/input-drop events observed by the capture callback
- the configured openSMILE descriptor columns, defaulting to all columns from `eGeMAPSv02` low-level descriptors

The VAD research file has one row per VAD grid frame. It includes the timing/sample columns and `vad`, where `-1` means VAD off/no data, `0` means silence, and `1` means speech.

The emotion research file has one row per emotion inference window. It includes `seq`, the sample window boundaries, `voiced_fraction`, `label`, `confidence`, and one score per emotion dimension: `angry`, `disgusted`, `fearful`, `happy`, `neutral`, `other`, `sad`, `surprised`, `unknown`.

The unsuffixed `track_<timestamp>.csv` is a legacy combined/status table. One row is written on the logger clock, controlled by `log_interval`; the default `0.25` seconds means 4 rows per second. It contains the latest known values from the independent streams plus the sample metadata of the latest openSMILE frame. Use it for quick inspection and backwards compatibility, not as the primary research data table.

### 4.3 Which CSV Should I Use?

Use the central collector CSV when you want a complete packet archive from multiple Pis and microphones in one place. It preserves all OSC topics and sender information.

Use the Pi-local research CSVs when you need correct sample alignment. Join openSMILE, VAD, and emotion by `sample_center` or by interval overlap on `sample_start`/`sample_end`, depending on the analysis question.

Use the unsuffixed Pi-local combined CSV when you want a compact operational table at the live logger rate.

For a live session, it is reasonable to run both: central collection for raw multi-device OSC capture, and Pi-local logging for per-microphone research tables.

## 5. Retrieving Pi-Local Logs

There are two normal ways to retrieve Pi-local CSV logs after a run.

### 5.1 Network Copy

If the central computer can SSH into the Pis, use `gather_logs.sh`:

```bash
./gather_logs.sh log_data/session_001 pi1.local pi2.local
```

If the repository lives at a different path on the Pi, pass the remote output path explicitly:

```bash
./gather_logs.sh --remote-path SPEECH_RECORD_ANALYSIS/log_data/ log_data/session_001 pi1.local
```

### 5.2 Physical SD-Card Copy

For long multi-day installations, it may be simpler to collect the Pis physically and copy the logs from the SD cards afterward.

1. Shut down the Pi cleanly if possible.
2. Remove the SD card and mount it on another computer.
3. Find the Pi user's home folder on the card.
4. Copy `SPEECH_RECORD_ANALYSIS/log_data/` to the central archive folder.

Because the Pi-local CSV files contain `session_start_unix_ms`, `session_start_iso`, `timestamp_unix_ms`, and `timestamp_iso`, the files still carry full date/time information even if they are copied days later.

## 6. Timing, Sampling, And Model Rates

The processing stages do not all run at the same rate.

| Stage                  | Default timing                                                  | Notes                                                                                      |
| ---------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Audio capture          | Native device rate, resampled to 16 kHz                         | The input device may run at 44.1 or 48 kHz; the callback resamples to `sample_rate`.       |
| VAD                    | `vad_interval: 0.25`, `config_features.yaml` `vad.grid_hz: 100` | Silero VAD inspects overlapping audio windows and writes a 100 Hz speech/silence timeline. |
| openSMILE prosody      | `opensmile_interval: 0.5`                                       | openSMILE low-level descriptors are frame-level features with roughly 10 ms hop timing.    |
| Emotion                | `emo_window: 2`, `emo_hop: 0.5`                                 | emotion2vec reads a sliding 2 s window every 0.5 s when enabled.                           |
| Pi-local research CSVs | frame/window-driven                                             | openSMILE, VAD, and emotion files write every produced frame/window, with sample columns.  |
| Pi-local combined CSV  | `log_interval: 0.25`                                            | Writes the latest known VAD/prosody/emotion values at 4 Hz by default.                     |
| OSC stream             | logger-driven                                                   | Sends VAD, selected prosody features, and emotion scores on the logger tick.               |
| Central collector      | packet-driven                                                   | Records every incoming OSC packet at the central computer receive time.                    |

Because the models run independently, the unsuffixed combined CSV may contain a new VAD value, the latest prosody frame, and an emotion result computed from an earlier 2 s window. This is expected. The combined row is a reporting grid, not proof that every model produced a new result at that exact millisecond. The independent research CSVs keep each stage on its own natural frame/window grid and expose the sample interval needed for correct alignment.

## 7. Data Values And Ranges

### 7.1 VAD

`vad` is a gate, not a probability, in the CSV output:

| Value | Meaning                                                                              |
| ----- | ------------------------------------------------------------------------------------ |
| `-1`  | VAD is disabled or no VAD data exists yet. Downstream stages treat the gate as open. |
| `0`   | VAD is active and the current interval is silence/non-speech.                        |
| `1`   | VAD is active and the current interval contains speech.                              |

Silero VAD itself uses `vad_threshold` internally. The default in the example config is:

```yaml
vad_threshold: 0.3
```

### 7.2 Prosody And openSMILE

The live OSC/browser view streams a small subset of openSMILE low-level descriptors:

| OSC / CSV key                 | Approximate display range         | Meaning                                                            |
| ----------------------------- | --------------------------------- | ------------------------------------------------------------------ |
| `F0semitoneFrom27.5Hz_sma3nz` | 0 to 50 semitones                 | Pitch estimate. Zeros/invalid unvoiced frames are treated as gaps. |
| `Loudness_sma3`               | 0 to 2.5                          | Perceptual loudness descriptor.                                    |
| `jitterLocal_sma3nz`          | 0 to 0.35                         | Cycle-to-cycle voice perturbation.                                 |
| `shimmerLocaldB_sma3nz`       | 0 to 30 dB                        | Amplitude perturbation.                                            |
| `HNRdBACF_sma3nz`             | 0 to 15 dB in the browser display | Harmonics-to-noise ratio.                                          |

The project uses openSMILE eGeMAPSv02 features. For background, see the openSMILE Python package documentation and the eGeMAPS feature set:

- https://audeering.github.io/opensmile-python/
- https://audeering.github.io/opensmile-python/api/opensmile.FeatureSet.html

The older `src/track_writer.py` helper documents an utterance-level 88-column eGeMAPSv02 CSV shape, but the current `strip_monitor.py` runtime writes the live low-level descriptor subset listed above.

### 7.3 Emotion

Emotion scores come from emotion2vec through FunASR. The CSV keeps a top label plus one score per class:

```text
angry, disgusted, fearful, happy, neutral, other, sad, surprised, unknown
```

Treat these scores as model outputs useful for comparison over time, not calibrated psychological measurements. `emo_confidence` is the score of the current top label. The model reads a sliding audio window, so rapid emotion changes will lag behind the microphone signal by design.

### 7.4 Self Telemetry

When OSC streaming is active, each Pi process also emits:

```text
/dev/<device_id>/stats/self <rss_mb> <cpu_pct> <temp_c> <n_threads>
/dev/<device_id>/stats/rate <address> <hz>
```

The central collector records these messages too. They help diagnose whether two mic processes are overloading a Pi.

## 8. Runtime Switches

The browser receiver and OSC control commands can switch stages on and off while the Pi process keeps running.

| Control                     | Effect                                                                               |
| --------------------------- | ------------------------------------------------------------------------------------ |
| OSC on/off                  | Starts or pauses outgoing OSC telemetry. The Pi process continues analyzing locally. |
| Log start/pause/resume/stop | Opens, pauses, resumes, or closes the Pi-local CSV file.                             |
| AUDIO reconnect             | Re-scans/open the configured `MIC1` or `MIC2` device after a USB reconnect.          |
| VAD on/off                  | Enables or disables Silero VAD gating.                                               |
| PROS on/off                 | Enables or disables openSMILE prosody extraction.                                    |
| EMO on/off                  | Enables or disables emotion inference if the model was loaded at startup.            |

Important distinction: `emotion_active` can be toggled at runtime only if `emotion_load: true` was used at process startup. If `emotion_load: false`, the model is not loaded into memory and emotion cannot be turned on later in that same process.

## 9. Why VAD Matters

VAD should normally be active during real sessions.

When VAD is active, it gates the analysis so silence and background noise are not treated as meaningful speech. Prosody features that depend on voicing, such as pitch, jitter, shimmer, and HNR, are blanked/gapped during silence. Emotion inference also checks the voiced fraction of its analysis window before running.

When VAD is off, the system treats the gate as open. This is useful for debugging because it forces data to flow, but it also means the prosody and emotion stages may process room noise, handling sounds, silence, or electrical noise. In other words, without VAD active, the pipeline can try to interpret garbage as speech.

Use VAD off only when you deliberately want an ungated diagnostic stream.

## 10. Extending The Collected Features

More openSMILE low-level descriptors can be logged or exposed live if needed. The current live OSC subset is a visualization choice, not a hard limit of openSMILE.

For research logging, edit `config_features.yaml`:

```yaml
opensmile:
  feature_set: eGeMAPSv02
  feature_level: LowLevelDescriptors
  log_features: all
  osc_features:
    - F0semitoneFrom27.5Hz_sma3nz
    - Loudness_sma3
```

Use `log_features: all` for the full openSMILE DataFrame columns, or provide a list of exact openSMILE column names for a narrower file. `osc_features` should stay small because it drives live network visualization.

To add another browser-visible live feature:

1. Find the exact openSMILE low-level descriptor column name.
2. Add it to `opensmile.osc_features` in `config_features.yaml`.
3. If the feature should appear in the browser GUI, add a matching entry to the `channels` object in `receiver/sketch.js`. That controls display label, color, and plot range.

The central collector does not need a schema change because it records arbitrary OSC addresses in long format.
