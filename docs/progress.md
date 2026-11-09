# Decisions and progress

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Model](model.md) · [Data pipeline](data-pipeline.md)

## Current handoff — 2026-11-09 (night)

Roadmap item 4 is done: the reactor bank runs as its own device service (`python -m algae_sim device serve`) and AutoLab drives it over HTTP through the `algae-sim-device` adapter. `scripts/resume_demo.sh` SIGKILLs AutoLab 13 h into a 36 h hold; the device keeps all three reactors near 30 °C, and AutoLab restarts, confirms each hold and completes the experiment with all 18 OD readings. Outage length decides the QC verdict, not the resume: an 8.4 h unwatched gap (`OUTAGE_S=30`) still passed (1 of 6 readings missed, coverage just above 75%), while a 16.5 h gap (`OUTAGE_S=60`) failed `readings_complete` and `telemetry_coverage` on every replicate. 61 tests pass, including a crash-and-resume campaign in lockstep mode.

Next: the Azure data pipeline (needs service and cost choices first), or polish such as a live device dashboard.

## 2026-11-09 — The reactor as a device

- **Decision:** extract the firmware (`DeviceCore`, stdlib only) and host it either in-process or in an HTTP service, so the two adapters can't drift apart.
- **Decision:** stdlib `http.server` + JSON, keep-alive, localhost only. No framework, no new dependencies.
- **Decision:** the device owns its clock. Lockstep mode for tests; paced mode for realism. The adapter reconciles AutoLab's simulated clock with the device clock in both directions.
- **Decision:** network failures are classified (unreachable / no reply / refused); lost replies are resolved by idempotent resend; persistent silence is ambiguous.
- **Decision:** injected faults are device settings (`configs/device-settings.json`), since they belong to the hardware, not to the campaign.
- **Finding:** at 7200× and 1800× the resume demo failed QC. AutoLab's restart (4–20 real seconds) was 4–10 simulated hours of unwatched culture, and sequential setpoint commands skewed reactor start times beyond the 10-minute reading window. The demo now runs at 900× with 75% telemetry coverage and prints the observed gap.

## 2026-11-09 — Made shareable

- **Decision:** pin AutoLab by exact GitHub commit, never by local path, so anyone can clone and run it, and parallel AutoLab work can't break it.
- Added CI, `scripts/demo.sh`, `algae_sim.autolab.plot_campaign` (charts straight from AutoLab's evidence database) and README images.
- Note: campaign numbers differ slightly between separate campaigns because AutoLab gives each campaign new IDs and culture seeds come from experiment IDs. A rerun or resume of the *same* campaign is identical.

## 2026-11-09 — Stage-6 completion update

- **Decision:** fault injection is per reactor and per hold (`heater_faults`, `culture_crashes`), replacing the global batch counter, so individual replicates can be targeted.
- **Decision:** replicate seeds come from the experiment ID plus the reactor ID.
- Telemetry reports `active_command_id`. Because the simulated reactors don't outlive the process, AutoLab resume safely ends the hold; this is documented as a known limitation.
- Reactor gained a culture-crash fault (biomass loss from a set hour) to exercise AutoLab's contamination screens.
- Earlier the same day an editable `../autolab` dependency broke the end-to-end test when the parallel AutoLab session changed the config format, and my commit went in with that test failing because piping pytest through `tail` hid it. AutoLab is now always pinned to a commit, and test exit codes are checked before committing.

## 2026-11-09 — AutoLab culture controller

- **Decision:** implement AutoLab's `CultureEnvironmentController` here (as `algae-sim-reactor`), not in AutoLab, so AutoLab stays free of any particular device. The OD probe is integrated through `IntegratedCultureSensor`.
- **Decision:** the adapter advances the simulation lazily to the clock's `now()` on every call, rather than subscribing to clock events, so the same code would work with a wall clock.
- **Decision:** each accepted setpoint command starts a fresh batch. The seed comes from the experiment ID so it's resume-safe. Fault injection is through adapter-owned `settings` in the campaign config.
- Reactor gained a safe state (heater and lights off) and running light-energy totals. The OD sensor can now take an external clock and any reactor lookup.

## 2026-11-08 — Refocus

- **Decision:** algae-sim becomes AutoLab's simulated bioreactor, which matches AutoLab roadmap stage 6 (biological culture). The 5-device Azure fleet plan is dropped. The Azure work shrinks to a separate, optional instrument-data pipeline demonstration.
- **Decision:** move from hand-coded learning steps to agent-built, Steven-reviewed slices with walkthroughs (see AGENTS.md). Steven wants to understand and explain the engineering, not hand-type it.
- **Decision:** integrate through AutoLab's existing seams only: the `autonomous_lab.sensor_adapters` entry point and the ask/tell optimiser. AutoLab is pinned to pushed commit `97561db` (v0.5.0) and its repo is untouched; at the time it had someone else's uncommitted work in progress.
- **Decision:** switched to a uv-managed project on Python 3.12 (matching AutoLab). The core package stays standard-library only; AutoLab and matplotlib are optional extras.
- Replaced `thermal_sim.py` with the `algae_sim` package. The milestone-1 check (30.25 °C after 10 minutes at full heat) is kept as a test.
- Known gap: the demo loop does its own execution step because AutoLab v0.5's campaign runner is assay-specific. See architecture.md.

## 2026-09-20 to 2026-10-01 — Milestone 0–1 (historical)

Set up the repository and venv. Built and hand-studied a one-second Euler thermal model with fixed heater input, plus 8 unittest checks: equilibrium at 40 °C, agreement with the exact solution, and history recording. The full history is in Git.
