# Decisions and progress

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Model](model.md) · [Data pipeline](data-pipeline.md)

## Current handoff — 2026-11-09

algae-sim now drives AutoLab's real stage-6 culture campaigns. A 20-experiment campaign through `autolab culture run` completes in about 3 s with 19 observed and 1 QC-failed; the failed batch is the injected heater fault, caught by `temperature_control` (mean deviation 10.0 °C). The best results cluster at 26–31 °C and 130–220 µmol/m²/s. All 120 OD readings are reported as simulated evidence. 46 tests pass, including an end-to-end campaign through AutoLab's CLI.

Dependency note: AutoLab's stage-6 work isn't pushed, so `pyproject.toml` pins AutoLab to local commit `b40e651` (`stage6-culture-slice-b`). It was briefly an editable path dependency. That broke the end-to-end test when the parallel AutoLab session began changing the culture config format (`reactor_id` → `reactor_ids` for replicates, and `active_command_id` for resume). Once that work lands, update the adapter and config and re-pin to the pushed GitHub commit.

Next decision for Steven: start the Azure pipeline (choose services and cost ceiling first), or the next AutoLab stage-6 slice (parallel reactors with batch proposals, mid-hold resume).

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
