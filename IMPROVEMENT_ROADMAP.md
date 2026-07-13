# Improvement Roadmap

Last updated: 2026-07-09

Purpose: capture medium/long-term project improvements (separate from immediate operational next steps in NEXT_STEPS.md).

## Guiding Principle

Prioritize deployment safety over aesthetics. Improve structure in stages, with compatibility wrappers first, then internal refactors, then docs migration.

## Current Constraints

- The repo currently depends on many root-level script/config names.
- Multiple scripts/docs assume fixed paths and command names.
- Fleet deployment and autostart workflows are path-sensitive.
- A full folder reshuffle is high-risk unless done with a staged compatibility plan.

## Priority A: Reliability And Operability (High Impact, Low Risk)

### A1. Dependency determinism

- Keep requirements-pi.txt authoritative for Pi runtime.
- Add explicit import-to-requirement checks in CI/preflight.
- Track optional vs required dependencies clearly.

Success criteria:

- No runtime ImportError on freshly provisioned Pi.
- Wheelhouse build succeeds from requirements-pi.txt without manual fixes.

Estimated effort: 1 to 2 hours.

### A2. Headless-first contract

- Formalize headless mode as production default in docs and scripts.
- Keep display mode as legacy/optional path until explicitly revived.
- Add a smoke test command for headless startup and OSC heartbeat.

Success criteria:

- One documented headless command works on every target Pi.
- Operators do not depend on local matplotlib UI.

Estimated effort: 1 hour.

### A3. Deployment preflight script

- Create one command that validates:
  - required files present
  - wheelhouse completeness
  - config parse sanity
  - expected ports/status behavior
- Fail fast with actionable messages.

Success criteria:

- Preflight catches common deployment mistakes before fleet rollout.

Estimated effort: 2 to 3 hours.

## Priority B: Safe Structure Cleanup (Medium Risk)

### B1. Introduce compatibility wrappers (no file moves yet)

- Keep current root entrypoints.
- Add thin wrappers that call future internal locations.
- Start routing docs to preferred wrapper commands.

Success criteria:

- Existing operator habits keep working.
- New structure can evolve behind stable command names.

Estimated effort: 2 to 4 hours.

### B2. Internal folder layout (phase-in)

Proposed target layout:

- runtime/
  - strip_monitor.py
  - audio_analysis_background.py
  - configs/
- deploy/
  - prepare_wheelhouse.sh
  - install_from_bundle.sh
  - deploy_bundle_to_fleet.py
  - configure_auto_start.py
- control/
  - speech_control.py
  - broadcast_ctrl.py
  - save_and_pull_logs.py
- web/
  - run_web.sh
  - receiver/
- docs/
- src/

Migration method:

- Move one domain at a time.
- Keep root wrappers with original names.
- Update tests/docs after each domain move.

Success criteria:

- No break in existing commands.
- Script map reflects both compatibility and new internals.

Estimated effort: 4 to 8 hours total (staged).

### B3. Path centralization

- Introduce one shared path constants module or shell include.
- Replace duplicated hardcoded assumptions in scripts/docs.

Success criteria:

- Changing one install root does not require many edits.

Estimated effort: 2 to 3 hours.

## Priority C: Quality Gates And Regression Control

### C1. Minimal automated test matrix

- Add smoke tests for:
  - headless startup
  - config load
  - OSC hello/state
  - deployment preflight
- Add one test for wheelhouse presence/shape checks.

Success criteria:

- Critical workflows validated before deployment.

Estimated effort: 3 to 5 hours.

### C2. Documentation hardening

- Define canonical docs and deprecate duplicates.
- Add migration notes when command behavior changes.
- Keep one operator quickstart page and one deploy runbook.

Success criteria:

- New operator can execute end-to-end flow without tribal knowledge.

Estimated effort: 2 to 4 hours.

## Recommended Execution Plan

- Sprint 1 (safe, short): A1 + A2 + A3.
- Sprint 2 (controlled cleanup): B1 + B3.
- Sprint 3 (actual internal moves): B2 in small slices.
- Sprint 4 (stabilization): C1 + C2.

## Not Recommended Under Time Pressure

- Big-bang file reshuffle without wrappers.
- Simultaneous structure change + deployment rollout.
- Editing many docs/scripts in one untested pass.

## Decision Rule For Future Refactor Sessions

Proceed with structural changes only if all conditions hold:

- at least 2 to 4 focused hours available
- can run preflight/smoke tests after each step
- wrappers preserved for root entry commands
- rollback path documented before moving files
