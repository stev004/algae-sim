# Decisions and progress

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Model](model.md) · [Data pipeline](data-pipeline.md)

## Current handoff — 2026-11-11

algae-sim speaks AutoLab's **culture contract v2** and is pinned to AutoLab `e9f8421`. The device declares named controlled variables (temperature, light intensity, pH), protection limits and metered resources (energy, CO2). pH is a setpoint the device holds by dosing CO2, with an illustrative carbon model, a pH probe, a pH controller with low/high trips, and stuck-valve faults. The demo campaign optimises all three variables against OD750, energy and CO2. The pipeline capture format is now schema v3 with pH and CO2. 106 tests pass; `demo.sh`, `resume_demo.sh` and `pipeline_demo.sh` all run, and the pipeline's flags still match AutoLab's QC replicate for replicate, including the new CO2-valve fault.

Next: the Azure version of the pipeline, with Steven closely involved (account, region, Data Explorer vs SQL, cost guard and teardown). See [data-pipeline.md](data-pipeline.md). A dashboard and write-up follow once the architecture settles.

## 2026-11-11 — Culture contract v2: pH held by CO2

- **Decision (agreed with the AutoLab session and Steven):** pH is held by the device, not set independently. The optimiser chooses a pH setpoint; CO2 used is a measured consumption, like energy. Nitrate, dissolved O2 and stirring are out of scope.
- **Decision:** the device manifest is the single source of truth for variable names, units, ranges, resolution, protection limits and resources. AutoLab only intersects them with its policy. The table lives once in `device/core.py` and both adapters convert from it.
- **Decision:** a command must name exactly the device's variables. Unknown and missing names are both refused by the firmware, not the adapter, so the refusal is the device's own.
- **Decision:** the carbon model is one lumped buffer coefficient plus gas exchange towards an air-equilibrium pH (8.6). Gas exchange is what makes low pH cost CO2, giving the optimiser a real trade-off. pH_air is set above the campaign's range so every campaign setpoint is holdable; at 9.0 a young culture can't be held up, which QC correctly rejects.
- **Decision:** the low-pH trip closes a separate supply shutoff, so protection still works when the dosing valve itself is stuck open. A high-pH trip is an alarm only, as there's no base dosing.
- **Decision:** pickled device state is versioned; older state files are refused rather than misread. The capture schema went to v3 and the parser accepts only v3, since the local pipeline holds no data worth migrating.
- Fault-injection demo gained experiment 11: reactor-1's CO2 valve sticks open, the device trips on low pH, and AutoLab stops and rejects that replicate while 2 of 3 still pass.

## 2026-11-10 — Local data pipeline

- **Decision:** build the pipeline locally first, standard library only. Each stage has a narrow interface so the Azure version swaps storage and hosting, not rules. The local version is a stepping stone; Azure is the target.
- **Decision:** the device captures to an append-only log; a separate uploader moves it. The log is the buffer, a byte-offset cursor records acknowledgements, and delivery is at-least-once with dedupe on `(device_id, sequence)`.
- **Decision:** capture records what the device measured (periodic environment samples plus actual OD readings). It takes no extra OD readings, so capture can't change AutoLab's results.
- **Decision:** keep raw batches as received, before parsing, so clean tables can be rebuilt if a rule changes. Rejected lines are stored with a reason, never dropped silently.
- **Finding:** the uploader at first treated "capture log doesn't exist yet" as idle and quit before the campaign started. A missing log now means "waiting".
- Checked current Azure pricing and free tiers (Functions Flex Consumption, Data Explorer free cluster, SQL Database free tier, IoT Hub Free, the free account, budgets). Recorded in data-pipeline.md.

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
