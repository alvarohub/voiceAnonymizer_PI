# Logging, Saving, And Collecting Data

This document describes the current recording workflow only:

1. Start logging in RAM on each Pi mic process.
2. Stop and save to files (or discard).
3. Pull saved files to the control computer.

It intentionally focuses on logging/saving/collection and does not cover live monitoring internals.

## Quick Workflow

1. Start a session with a RAM limit (minutes):

   python speech_control.py broadcast --session start_recording_session.yaml log_start 60

   If the minutes argument is omitted, the default is 60.

2. Save all processes with a common base name:

   python save_and_pull_logs.py --session start_recording_session.yaml take_001

   This command sends save to all expected processes, collects save confirmations, and pulls files to the control computer.

3. If you want to discard instead of saving:

   python speech_control.py broadcast --session start_recording_session.yaml log_discard_stop

## Start Logging Behavior

When log_start is issued, each mic process opens a RAM-backed session.

- Parameter: max_minutes
- Default: 60
- If elapsed recording time reaches max_minutes:
  - no new rows are added to RAM buffers
  - the session remains open
  - you can still run save or discard

This prevents uncontrolled memory growth during long runs.

## Save Behavior

When save is issued, each process writes only the feature files:

- _opensmile_lld.csv
- _vad.csv
- _emotion.csv

Combined file output is not part of this workflow.

### Save Name Rules

- If you provide a name (example: take_001), it is used as the base.
- If no name is provided, the process auto-builds one using:
  - pi id
  - mic id
  - date/time
  - recorded length in seconds

### Save Confirmations

Each process sends:

1. Final save acknowledgment for the save command.
2. One per-file saved notice including:
   - file kind
   - file name
   - full file path on the Pi

The pull step uses these paths directly.

## Collecting Files To Control Computer

Use:

python save_and_pull_logs.py --session start_recording_session.yaml [optional_base_name]

What it does:

1. Loads expected targets from the session file.
2. Sends save to all targets.
3. Waits for save acknowledgments and per-file save notices.
4. Pulls files one by one over SSH using the reported file paths.

Default destination on control computer:

log_data/pulled/<timestamp>/

Per-device subfolders are created automatically.

Useful options:

- --ssh-user pi
- --dest-dir <local_folder>
- --ack-timeout <seconds>
- --scp-timeout <seconds>
- --dry-run

## Saved File Schema (Core)

The three saved files share this leading timing structure:

- name
- frameTime
- unix_start
- unix_end

Then:

- opensmile file: selected openSMILE columns (from config_features.yaml)
- vad file: vad
- emotion file: voiced_fraction, label, confidence, and emotion scores

## Session File Notes

The default session file is:

start_recording_session.yaml

For start-recording behavior, you can set:

logging:
  start: true
  max_minutes: 60

## Failure Handling

- If some process does not acknowledge save in time, the command reports timeout per process.
- If a file pull fails, the script reports that file and continues with the rest.
- Save and pull are separated by acknowledgments, so each step is observable and debuggable.
