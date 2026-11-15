# Decisions and progress

[Project overview](../README.md) · [Working agreement](../AGENTS.md) · [Architecture](architecture.md) · [Model](model.md) · [Data pipeline](data-pipeline.md)

## Current handoff — 2026-11-15

**State.** algae-sim is complete as a portfolio piece. The [case study](case-study.md) is the entry point.
- It speaks AutoLab culture contract v2, pinned to AutoLab `796aed3` (stage 8: parallel reactor batches).
- Growth uses cited *C. vulgaris* values, with self-shading ([model.md](model.md)).
- Against a real experiment, 9 of 12 runs pass at a 5 cm light path, but the temperature ranking is backwards ([validation.md](validation.md)).
- Benchmark: +2.9 % trade-off over random search, 22 of 30 runs, p < 0.001. With parallel batches of 3 on 9 reactors, the same result comes in 6 culture rounds instead of 18, at no detectable efficiency cost (p = 0.45) ([benchmark.md](benchmark.md)).
- The data pipeline runs locally and on Azure at £0 ([data-pipeline.md](data-pipeline.md)).
- 142 tests pass; `demo.sh`, `resume_demo.sh` and `pipeline_demo.sh` all run.

**Azure resources, all Steven's:**
- resource group `rg-algae-sim-pipeline` (UK South) on a free-trial subscription, holding storage account `stalgaesim1107` and its private container `raw`;
- the Data Explorer free cluster, database `algae`, with tables, views and functions from `algae_sim/azure/schema.kql`;
- the Entra user `aaduser=0f45a5f8-…;0235cd2d-…`, which has Ingestor and Viewer on `algae`;
- local addresses in `configs/azure.local.json` (git-ignored).

**Open threads:**
- **Azure free account lapses around 2026-12-14.** Steven moves next. Default: let it lapse; the subscription is disabled and nothing is charged. The storage account and blobs then go, which is fine, since they're regenerable from `runs/pipeline-demo`. The Data Explorer free cluster is separate and lasts a year, unless Microsoft withdraws the free tier. Optional tidy-up: delete `rg-algae-sim-pipeline` in the portal. If he upgrades instead, add a £5 budget alert first.
- **Open-source lab-automation contribution.** Steven is taking this to his separate Bio-OSS session, not this repo. Leading candidate: PyLabRobot. Starter issues: #1255–1260 (docs), #1243 (good first issue). Stronger second contribution: #1376, per-protocol time and consumption tracking. Nothing to do here.
- **Applications for lab-automation roles.** Steven moves next, with the `apply` skill, using the case study as the centrepiece.
- **Optional model work, only on Steven's agreement:** a strain-specific temperature curve and a nitrogen limit, each checked against a dataset it wasn't fitted to. Also optional: a small real-hardware loop behind the same device contract.

## 2026-11-15 — Parallel reactor batches (AutoLab stage 8)

- **Decision (Steven):** do Stage 8's first slice. He delegated the design review to the algae-sim session.
- **Design:** AutoLab ADR 0005 was reviewed and approved with two requests:
  - verify Optuna's `constant_liar` with 3 objectives and `constraints_func` (done, and tested);
  - report round, slot and the effective random start-up per experiment (done: `culture report` → `batching`).
- **Merge:** Steven approved the merge and push in the AutoLab session. Code `16636dc`, main `796aed3`; AutoLab CI passed. algae-sim now pins `796aed3`.
- **Regression:** the original 90 benchmark campaigns reproduce byte-identical on the new pin, so `batch_size: 1` is unchanged.
- **Batch arm:** `run_config("batch", …)` uses `batch_size: 3`, 9 reactors (in both `culture_design` and the controller settings), the same seeds, budget and start-up.
- **Result** (30 runs, 540 of 540 experiments passed QC):
  - at equal experiments, no detectable difference from one at a time (15 of 30, p = 0.45);
  - at equal culture time (6 rounds), batching is ahead in 30 of 30 (+0.042 hypervolume);
  - new chart: `docs/images/benchmark-batch*.png`. The strategy charts are byte-identical to before.
- **Finding:** the planned "rounds to reach the median" measure was ill-posed, because about half the runs miss a median by definition. It was dropped and documented; equal-experiments answers the question.

## 2026-11-15 — Case study and diagrams

- Added `docs/case-study.md`, linked at the top of the README: the system, one experiment, results (benchmark, validation, pipeline) and the Azure setup.
- **Decision (Steven):** the first Mermaid diagrams were too busy. They were replaced by two house-style SVGs (loop, Azure) in light and dark, drawn by `scripts/draw_diagrams.py` and checked by rendering before committing.
- Added a chart queried live in Data Explorer (`HoldSummary()` rendered as a column chart), cropped to hide account details: `docs/images/azure-data-explorer-chart.png`.
- Fixed stale architecture notes: seeding, and Azure no longer "planned". Corrected an overstated claim; it's AutoLab's *source* that has no simulator-specific code (checked).

## 2026-11-15 — Azure pipeline

- **Decisions (Steven):**
  - a new free account, for its hard spending cap;
  - the free Data Explorer cluster as the store;
  - UK South;
  - a £0 ceiling.

  He set up the resources himself in the portal.
- **Finding:** the free trial refuses Flex Consumption Functions, contrary to the docs. **Decision:** no Function; the laptop uploader writes to Blob and Data Explorer directly, signed in with Entra ID, so no keys exist.
- **Finding:** the free cluster refuses streaming ingestion (403) from an Entra ID user, even with the Ingestor role. **Decision:** queued ingestion, with a 30 s batching window.
- **Finding:** the Azure sign-in is a different principal from the Microsoft account that created the cluster. Steven granted it Ingestor and Viewer, least privilege. Permission errors now stop the uploader rather than retrying forever.
- **Result:**
  - the full demo capture went up in about 32 s, and a full replay was absorbed by the dedupe views (10,032 unique messages kept);
  - `HoldSummary()` matches the local report on all 45 holds.

## 2026-11-14 — Self-shading

- **Decision (agreed with Steven):** add Beer–Lambert self-shading, but not a carbon limit. The simulated reactor always doses CO₂, so a carbon limit would only fit the validation's air-only case.
- **Source:** k = 0.228 m²/g from Kuhfuß et al. 2022 (*C. vulgaris*, read and checked), not tuned to the validation data. The light path is 5 cm, illustrative, set before rerunning. The light response is averaged over 16 layers (within 0.002 of a fine reference).
- **Effect:** dense cultures now need more light; the demo's best culture moved to 278 µmol/m²/s, and ODs are lower and more realistic. All faults still fail QC, and the pipeline still matches.
- **Validation rerun:** 9 of 12 runs pass at 5 cm (median ratio 1.05×, was 2.1×). The result moves with the unreported vessel depth (5 of 12 at 3 cm, 8 of 12 at 8 cm). The temperature ranking is still backwards (−0.77).
- **Benchmark rerun:** +2.9 % hypervolume vs random search (22 of 30, p < 0.001), still no denser cultures; +13.5 % vs grid. The conclusion held through the model change.

## 2026-11-14 — Out-of-sample check against real data

- **Decision:** test the growth model against an independent experiment, with the rules fixed in advance: no fitting; pass if the early growth rate is within ±30 %. The data are Pinto et al. 2026, *Mar. Drugs* 24:124 (CC BY): *C. vulgaris* CCAP 211/11B, 12 batch runs at 18–32 °C, 55 µmol/m²/s, air only.
- **Result: fails.**
  - 0 of 12 runs pass; the model grows a median 2.1× too fast early, and its day-14 biomass is 2.3× too high.
  - It ranks temperatures nearly backwards for this strain (rank correlation −0.77): the strain grew best at 18–20 °C.
  - Likely causes: no light shading, no carbon limit under air-only aeration, no nitrogen limit, and a strain cooler-loving than the pooled parameters (consistent with Serra-Maia 2016, same strain).
- **Decision:** document rather than tune. A fix needs new mechanisms checked against a different dataset. The benchmark's limits now cite this.

## 2026-11-13 — Optimiser benchmark

- **Decision (agreed with Steven):** benchmark AutoLab's optimiser against random search and a grid. 30 paired runs per method, 18 experiments each. Score with 3-D hypervolume against a reference point from device limits only, plus best OD750; use distribution-free paired statistics.
- **Finding (from the AutoLab session):** AutoLab seeds each trial's sampler with campaign seed + index, so adjacent campaign seeds share start-up designs. Benchmark seeds are spaced 1000 apart.
- **Decision:** AutoLab gained an optional `optimization.grid_levels` setting (commit 780937b, approved by Steven) so a grid can name its levels. algae-sim now pins AutoLab `26bfc8b`, which adds only a README line on top.
- **Result:**
  - Against random search, the optimiser gives +2.5 % hypervolume (22 of 30 pairs better, 95 % CI +0.0011 to +0.0221, p < 0.001) but no difference in best OD (p = 0.80).
  - Against the grid, it gives +13.4 % hypervolume (30 of 30) and +5.1 % best OD.
  - On this smooth simulated surface random search is a strong baseline. Limits are in benchmark.md.
- **Sensitivity:** with 4 random start-up experiments instead of 8, the edge over random search grows to +5.5 % hypervolume (25 of 30). Comparing 4 against 8 directly is not significant (p = 0.13). The pre-chosen 8 stays the headline.

## 2026-11-11 — Charts redesigned for GitHub

- **Decision (Steven):** replace the one busy campaign chart with three single-message charts (`progress`, `tradeoffs`, `qc`) in light and dark, with titles that state the result, drawn by `algae_sim/autolab/plot_campaign.py`.
- A styling pass: layout measured in inches, a quieter QC grid, and colour-blind-checked colours. Fixed muddy, chopped text by turning off font hinting and rendering at 200 dpi (fc59f0f).

## 2026-11-11 — Chlorella light and pH from primary sources

- Read the full texts through Steven's OU Library access (values and citations only go into the repo): Choi & Lee 2011, Serra-Maia et al. 2016, Schwaderer et al. 2011 (web appendix), Bernard & Rémond 2012.
- **Decision:** replace the Steele light curve with the curve Schwaderer et al. fitted to *C. vulgaris* data (I_opt 142, I_k 23.5). Bright light now costs some growth (0.84 at 400 µmol/m²/s) rather than most of it (0.46).
- **Decision:** pH limits 3.0 / 7.5 / 9.0 from Choi & Lee's measurements (was 5.5 / 7.8 / 10, illustrative). Low pH is no longer bad for growth, only expensive in CO₂; high pH is now the risky side.
- **Finding:** Choi & Lee's temperature data, which the model was not built from, matches the pooled temperature curve to within about 0.1 at every point.
- **Finding:** the demo's best culture is now 29.9 °C, 234 µmol/m²/s, pH 7.55, and the optimiser concentrates on pH 7.3–7.85. All four faults still fail QC.

## 2026-11-11 — Chlorella vulgaris parameters

- **Decision:** replace illustrative growth values with published *C. vulgaris* values, each cited with a confidence level in model.md. They are literature-typical, pooled across strains, not a fit to one strain; the model is still not validated.
- **Finding:** the old illustrative μmax (0.08 /h) was already in the published range. The biggest changes are the temperature range (now 2.2 / 28.9 / 42.8 °C), the OD calibration (3.2 per g/L, so ODs read about 1.6× higher) and Xmax (2.3 g/L).
- **Finding:** on the new parameters the demo optimiser's best culture (29.7 °C, 147 µmol/m²/s, pH 7.95) lands next to the published optima (28.9 °C, 142, 7.8). All four injected faults still fail QC as designed.
- **Open:** the Steele light curve over-penalises bright light compared with Pozzobon 2022, and pH_min is probably too high. Both need full texts we haven't read (listed in model.md).
- Added a plain statement to the README that the code was written by AI agents under Steven's direction.

## 2026-11-11 — Reproducible campaigns

- **Finding:** rerunning the demo gave slightly different charts. The device seeded each culture from AutoLab's experiment ID, and AutoLab mints fresh random IDs for every campaign, so measured OD drifted by about ±0.02 and the optimiser's later choices diverged. Chart drawing itself was already deterministic.
- **Decision:** seed each culture from the device seed, the reactor and that reactor's culture (hold) number, which the device already persists. That is how a lab labels runs, and it depends on nothing the client sends. A test runs a campaign twice and compares the PNG bytes.
- **Finding:** the double heater fault sat in experiment 14, chosen by the optimiser. Once results changed, it picked 20.5 °C there, where a dead heater is harmless next to the 20 °C room, and QC rightly passed it. The fault moved to start-up experiment 6 (31.6 °C), whose conditions come from the seed alone.
- **Finding:** with the new seeds the pipeline stopped flagging the hold 11 trip. Trips were only visible in the 10-minute samples, AutoLab safes a tripped reactor within minutes, and safe-state samples are excluded, so detection had depended on a sample landing in that gap. **Decision:** the device logs an extra environment sample the moment a trip fires, like a real alarm log, without moving the regular schedule. Device state version 3, because a batch now remembers its last trip state.

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
