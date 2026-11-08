# Decisions and progress

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Model](model.md) · [Data pipeline](data-pipeline.md)

## Current handoff — 2026-11-08

The reactor simulator and AutoLab adapter are built and verified: 37 tests pass and ruff is clean. The demo campaign (20 trials, seed 7, about 6 s) converges on roughly 26–29 °C and 115–155 µmol/m²/s, near the model's built-in optimum of 30 °C and 150 µmol/m²/s. QC excludes the deliberately faulty trial (heater failed, mean deviation 11.4 °C). The Azure pipeline is designed but not started.

Next decision for Steven: start the Azure pipeline (choose services and cost ceiling first), or extend the AutoLab side (several reactors, or a native culture campaign in AutoLab).

## 2026-11-08 — Refocus

- **Decision:** algae-sim becomes AutoLab's simulated bioreactor, which matches AutoLab roadmap stage 6 (biological culture). The 5-device Azure fleet plan is dropped. The Azure work shrinks to a separate, optional instrument-data pipeline demonstration.
- **Decision:** move from hand-coded learning steps to agent-built, Steven-reviewed slices with walkthroughs (see AGENTS.md). Steven wants to understand and explain the engineering, not hand-type it.
- **Decision:** integrate through AutoLab's existing seams only: the `autonomous_lab.sensor_adapters` entry point and the ask/tell optimiser. AutoLab is pinned to pushed commit `97561db` (v0.5.0) and its repo is untouched; at the time it had someone else's uncommitted work in progress.
- **Decision:** switched to a uv-managed project on Python 3.12 (matching AutoLab). The core package stays standard-library only; AutoLab and matplotlib are optional extras.
- Replaced `thermal_sim.py` with the `algae_sim` package. The milestone-1 check (30.25 °C after 10 minutes at full heat) is kept as a test.
- Known gap: the demo loop does its own execution step because AutoLab v0.5's campaign runner is assay-specific. See architecture.md.

## 2026-09-20 to 2026-10-01 — Milestone 0–1 (historical)

Set up the repository and venv. Built and hand-studied a one-second Euler thermal model with fixed heater input, plus 8 unittest checks: equilibrium at 40 °C, agreement with the exact solution, and history recording. The full history is in Git.
