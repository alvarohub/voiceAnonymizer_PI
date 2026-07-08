# openSMILE Information

To perform real-time audio analysis with openSMILE, you can either use the command-line `SMILExtract` tool with a microphone source or build a streaming application using the `opensmile-python` wrapper and SMILEapi.

## Method 1: Command-Line Live Processing With SMILExtract

You can stream directly from your local microphone by compiling openSMILE with PortAudio enabled.

Ensure the PortAudio backend is available:

```bash
SMILExtract -H cPortaudio
```

Run a live configuration, such as prosody or emotion baselines, and log the output to the console or an ARFF/CSV file:

```bash
SMILExtract -C config/emobase_live4.conf
```

## Method 2: Python Real-Time Stream And Log

The Python package itself does not natively include PortAudio support, so you need to capture audio using PyAudio and stream the NumPy arrays incrementally to openSMILE's `cExternalAudioSource`.

Important project decision, July 2026: most online examples use PyAudio because it is an older and very common PortAudio binding. This does not mean PyAudio is inherently more correct for research logging. PyAudio and sounddevice both sit on PortAudio. In this project, sounddevice is acceptable and probably preferable because it provides NumPy arrays directly in the callback, exposes PortAudio callback timing (`inputBufferAdcTime`, `currentTime`) and overflow status, and avoids the extra bytes-to-NumPy conversion layer that PyAudio blocking examples usually show.

The problem to fix is not "sounddevice vs PyAudio". The problem is clocking: the research log must be aligned to captured sample indices, not to the time at which a VAD/openSMILE/logger thread happens to wake up.

```python
import pyaudio
import numpy as np
import opensmile

# 1. Initialize openSMILE for real-time feature extraction
smile = opensmile.Smile(
    feature_set=opensmile.FeatureSet.GeMAPSv01b,
    feature_level=opensmile.FeatureLevel.LowLevelDescriptors,
)

# 2. Setup PyAudio input stream
p = pyaudio.PyAudio()
stream = p.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=16000,
    input=True,
    frames_per_buffer=1024,
)

# 3. Real-time logging loop
print("Streaming audio... Press Ctrl+C to stop.")
try:
    while True:
        data = stream.read(1024)
        audio_data = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0

        # Process the raw chunk
        features = smile.process_signal(audio_data, 16000)

        # Log active features to standard output
        if features is not None:
            print(features.mean(axis=0))

except KeyboardInterrupt:
    stream.stop_stream()
    stream.close()
    p.terminate()
```

If you want to customize the analysis, decide:

- What specific acoustic features or task are you performing, such as VAD, speech emotion, or pitch?
- Do you want to log these streams to a CSV file, a database, or a WebSocket?

## openSMILE And VAD

openSMILE can perform Voice Activity Detection (VAD). Historically, openSMILE includes built-in signal-processing components designed to track and segment human speech from background noise. Rather than acting as a simple volume threshold, openSMILE's native VAD calculates a mixture of energy, spectral shape, and voice characteristics to determine speech presence.

However, while openSMILE's native C++ engine excels at extracting these components, the standard `pip`-installed `opensmile-python` package is primarily streamlined to output acoustic feature DataFrames, such as ComParE or eGeMAPS.

For a highly precise, production-grade VAD module directly inside Python code, standard industry practice frequently pairs openSMILE's feature extraction pipelines with dedicated, neural-network-backed Python VAD libraries like Silero VAD or Pyannote.audio.

This is the approach used in this project.

## VAD And openSMILE Integration

To capture high-quality conversational data for future research, sync the VAD model with openSMILE's frame step size. If these windows do not align, you can get overlapping features or mismatched timestamps that make analysis difficult.

In academic speech analysis, features are calculated using a sliding window. The relationship between the audio sampling rate, window length, and overlap is important.

### Standard Research Parameters

1. **Audio sample rate:** 16,000 Hz / 16 kHz.
   This is the standard for speech processing with tools such as Silero, ComParE, and eGeMAPS. Higher rates like 44.1 kHz only capture frequencies outside the speech band for this use case.

2. **Window length / interval:** 20 ms to 25 ms.
   This is the duration of one audio block. Speech signals are assumed to be stationary, or stable, within this short timeframe.

3. **Hop size / overlap margin:** 10 ms / 50% to 60% overlap.
   This is the distance the window slides forward. A 10 ms hop size gives exactly 100 feature vectors per second, matching Silero VAD's default resolution.

Using a 25 ms window with a 10 ms hop size gives frames like this:

```text
Frame 1: 0.00s to 0.025s
Frame 2: 0.01s to 0.035s
Frame 3: 0.02s to 0.045s
```

## Live Streaming And Real-Time Logging Code

This complete Python script captures microphone audio in 30 ms chunks, matching Silero's optimal processing block size, processes the audio through Silero VAD to detect speech presence, extracts openSMILE acoustic features, and logs them in real time with high-precision timestamps.

```python
import time
import csv
import numpy as np
import pyaudio
import torch
import opensmile

# --- Configuration Constants ---
SAMPLE_RATE = 16000
CHUNK_DURATION_S = 0.030  # 30ms audio chunks (Silero VAD requirement)
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_S)  # 480 samples
LOG_FILE = "speech_experiment_log.csv"

# --- 1. Initialize Silero VAD ---
# Model loads locally via torch hub
model, utils = torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", force_reload=False)
(get_speech_timestamps, _, read_audio, *_) = utils

# --- 2. Initialize openSMILE ---
# eGeMAPS is the standard baseline for human conversation and emotion research
smile = opensmile.Smile(
    feature_set=opensmile.FeatureSet.eGeMAPSv02,
    feature_level=opensmile.FeatureLevel.Functionals,  # Or LowLevelDescriptors for frame-by-frame
)

# --- 3. Initialize CSV File for Data Logging ---
# Prepare the log headers dynamically based on openSMILE feature names
dummy_features = smile.process_signal(np.zeros(CHUNK_SIZE, dtype=np.float32), SAMPLE_RATE)
feature_names = list(dummy_features.columns)
csv_headers = ["Timestamp_Unix", "Timestamp_Relative_Sec", "VAD_Speech_Active"] + feature_names

with open(LOG_FILE, mode="w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(csv_headers)

# --- 4. Setup Audio Hardware Stream ---
p = pyaudio.PyAudio()
stream = p.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=SAMPLE_RATE,
    input=True,
    frames_per_buffer=CHUNK_SIZE,
)

print(f"Logging started. Saving data to: {LOG_FILE}")
print("Press Ctrl+C to safely stop the experiment.")

start_time = time.time()

try:
    while True:
        # Capture raw audio from the room
        raw_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        current_unix_time = time.time()
        relative_time = current_unix_time - start_time

        # Convert audio buffer to normalized float tensor for Silero (-1.0 to 1.0)
        audio_int16 = np.frombuffer(raw_data, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        audio_tensor = torch.from_numpy(audio_float32)

        # Execute VAD check
        # returns a probability float between 0.0 and 1.0
        speech_prob = model(audio_tensor, SAMPLE_RATE).item()
        is_speech = 1 if speech_prob > 0.5 else 0

        # Execute openSMILE feature extraction
        features_df = smile.process_signal(audio_float32, SAMPLE_RATE)
        feature_values = features_df.values.flatten().tolist()

        # Build complete log row
        log_row = [current_unix_time, round(relative_time, 4), is_speech] + feature_values

        # Log instantly to disk (append mode)
        with open(LOG_FILE, mode="a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(log_row)

except KeyboardInterrupt:
    print("\nExperiment stopped by operator. Closing streams safely.")
finally:
    stream.stop_stream()
    stream.close()
    p.terminate()
```

Why This Setup Works for Research

- Granular Timestamps: It logs both the exact Unix Epoch timestamp (great for syncing with other biosensors, video cameras, or eye-trackers) and a relative timeline counter starting from 0.0 seconds.
- eGeMAPSv02 Feature Set: It uses the Extended Geneva Minimalistic Acoustic Parameter Set. This is the gold standard in modern clinical, psychological, and speech-interaction research because it strips away redundant acoustic metrics to focus strictly on pitch variability, spectral gradients, and voice quality indicators.
- VAD State Inclusion: By saving VAD_Speech_Active as a binary flag (1 or 0) alongside the data rows, you can easily filter your dataset during post-analysis. You can isolate speech data or analyze the acoustic traits of background environmental noise during silences.

## References And Implementation Notes For This Project

This section is meant to justify the next code changes in `strip_monitor.py`. The current code works for live visualization, but the log should become sample-clocked so it is suitable for later research analysis.

### Baseline Preservation

Before changing the sync/logging architecture, preserve the existing working behavior with git. A branch is better than copying the old code into another folder because it avoids duplicate code drifting out of sync.

Current preservation branch created before the sample-clock refactor:

```bash
git branch backup/pre-sample-sync-20260708
```

Note: a branch protects the committed repository state. Uncommitted files still need to be committed or otherwise saved if they matter.

### openSMILE Python Frame Timing

The official `opensmile-python` usage docs show that `FeatureLevel.LowLevelDescriptors` returns a DataFrame indexed by frame `start` and `end`, not just a single value for the whole chunk. For eGeMAPSv02 LLDs, the examples show 20 ms windows with 10 ms steps:

```python
import opensmile

smile = opensmile.Smile(
    feature_set=opensmile.FeatureSet.eGeMAPSv02,
    feature_level=opensmile.FeatureLevel.LowLevelDescriptors,
)

df = smile.process_signal(signal, sampling_rate)
starts = df.index.get_level_values("start")
ends = df.index.get_level_values("end")
```

The documentation example prints rows like:

```text
start                  end
0 days 00:00:00        0 days 00:00:00.020000
0 days 00:00:00.010000 0 days 00:00:00.030000
0 days 00:00:00.020000 0 days 00:00:00.040000
```

Implication for this project: when a chunk sent to openSMILE begins at absolute sample `chunk_start_sample`, every returned row should be converted to absolute sample coordinates:

```python
frame_start_sample = chunk_start_sample + round(start_s * SAMPLE_RATE)
frame_end_sample = chunk_start_sample + round(end_s * SAMPLE_RATE)
frame_center_sample = (frame_start_sample + frame_end_sample) // 2
frame_time_s = frame_center_sample / SAMPLE_RATE
```

The CSV should log these sample indices. Wall-clock timestamps can be derived or estimated, but they should not be the primary alignment key.

References:

- openSMILE Python usage: https://audeering.github.io/opensmile-python/usage.html
- `Smile.process_signal()` API: https://audeering.github.io/opensmile-python/api/opensmile.Smile.html#opensmile.Smile.process_signal
- `FeatureLevel.LowLevelDescriptors`: https://audeering.github.io/opensmile-python/api/opensmile.FeatureLevel.html

### sounddevice Versus PyAudio

PyAudio and sounddevice are both Python interfaces to PortAudio. The popular PyAudio pattern is:

```python
import pyaudio
import numpy as np

p = pyaudio.PyAudio()
stream = p.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=16000,
    input=True,
    frames_per_buffer=480,
)

raw = stream.read(480, exception_on_overflow=False)
audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
```

This is fine, but it is not automatically better. PyAudio blocking reads can hide timing mistakes if code timestamps each chunk after `stream.read()`. That timestamp is the time the program received the block, not necessarily the capture time of the first sample.

The sounddevice callback pattern gives NumPy arrays directly:

```python
import queue
import sounddevice as sd

audio_q = queue.Queue()

def callback(indata, frames, time_info, status):
    if status:
        print(status)
    audio_q.put((indata[:, 0].copy(), frames, time_info, status))

stream = sd.InputStream(
    samplerate=16000,
    channels=1,
    dtype="float32",
    callback=callback,
)
stream.start()
```

The sounddevice stream docs state that the callback receives:

- `indata`: NumPy array shaped `(frames, channels)`.
- `frames`: number of captured frames in this callback.
- `time.inputBufferAdcTime`: ADC capture time of the first input sample, on PortAudio's stream clock.
- `time.currentTime`: callback invocation time on the same clock.
- `status`: flags for underflow/overflow conditions.

That is exactly what a research logger needs. The callback still should do almost no work: copy or enqueue the audio, update sample counters, record overflow flags, and return quickly. openSMILE, VAD, emotion inference, CSV writes, and OSC sends should remain outside the audio callback.

References:

- sounddevice stream API: https://python-sounddevice.readthedocs.io/en/latest/api/streams.html
- sounddevice examples using callback + queue: https://python-sounddevice.readthedocs.io/en/latest/examples.html
- sounddevice overflow flags: https://python-sounddevice.readthedocs.io/en/latest/api/misc.html#sounddevice.CallbackFlags
- PyAudio stream API: https://people.csail.mit.edu/hubert/pyaudio/docs/#class-pyaudio-stream

Conclusion: do not migrate to PyAudio just to match internet examples. Stay with sounddevice unless a deployment environment has a specific PyAudio-only requirement. The professional fix is to make the sounddevice path sample-accurate.

### Silero VAD Alignment

Silero VAD supports 8 kHz and 16 kHz audio and returns speech timestamps in samples by default. The README example uses:

```python
import torch

model, utils = torch.hub.load(
    repo_or_dir="snakers4/silero-vad",
    model="silero_vad",
)
(get_speech_timestamps, _, read_audio, _, _) = utils

speech_timestamps = get_speech_timestamps(
    audio,
    model,
    sampling_rate=16000,
    return_seconds=False,  # default: samples
)
```

For this project, keep VAD on the same 16 kHz sample clock as openSMILE. If the analysed VAD chunk begins at `chunk_start_sample`, convert each Silero segment to absolute samples:

```python
speech_start = chunk_start_sample + segment["start"]
speech_end = chunk_start_sample + segment["end"]
```

Then project the VAD result onto the same 10 ms / 160-sample frame grid used for openSMILE LLD rows. This avoids the current risk where VAD and openSMILE are produced by different threads using different `time.time()` calls.

Reference:

- Silero VAD README and examples: https://github.com/snakers4/silero-vad

### Recommended Research Log Schema

For frame-level acoustic logging, prefer one row per openSMILE LLD frame. At 16 kHz and 10 ms hop, this is 100 rows per second.

Recommended core columns:

```text
session_start_unix_ms
session_start_iso
device_id
sample_rate
frame_index
sample_start
sample_end
sample_center
time_s
time_ms
timestamp_unix_ms_est
vad
vad_ratio
audio_overflow
opensmile_* feature columns
emotion_label
emotion_confidence
emotion_* score columns
```

Important distinction:

- `sample_*` and `time_s` are the scientific timeline.
- `timestamp_unix_ms_est` is for approximate alignment with other devices.
- OSC can send the latest values at a lower rate and may lag.
- CSV should write frame rows based on captured samples, not based on logger wake-up time.

### Practical Changes Implied For `strip_monitor.py`

1. Keep sounddevice as the audio input backend.
2. In the audio callback, store blocks with absolute `start_sample`, `end_sample`, PortAudio ADC time, status flags, and the float32 samples.
3. Move the rolling buffer from a list of anonymous arrays to a sample-indexed buffer.
4. When openSMILE processes a chunk, pass the audio plus its `chunk_start_sample`; convert openSMILE DataFrame `start`/`end` into absolute samples.
5. When VAD processes a chunk, pass the audio plus its `chunk_start_sample`; convert Silero segment samples into absolute samples and then onto the same 100 Hz frame grid.
6. Make CSV logging consume frame records from the frame buffer rather than periodically copying only the latest frame.
7. Keep OSC as a separate visualization stream that can downsample or send latest values.
