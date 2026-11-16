# Case study: a closed-loop lab, end to end, before any hardware exists

[Project overview](../README.md) · [Architecture](architecture.md) · [Model](model.md) · [Benchmark](benchmark.md) · [Validation](validation.md) · [Data pipeline](data-pipeline.md)

**In one paragraph:** I built a simulated algae photobioreactor that behaves like lab hardware, and drove it with [AutoLab](https://github.com/stev004/autolab), my closed-loop experimentation platform. The engineering is the point: the device owns its own control and safety, and it survives its client crashing mid-culture. QC catches injected faults, and no measurement is lost or double-counted on the way to the cloud. The science is simulated, and I say where it's weak: against a real experiment the growth model gets the typical rate right but the temperature preference wrong. The optimiser's edge over random search turned out to come from cheap, thin cultures. The code was written by AI coding agents under my direction. I designed the system, set the requirements and reviewed the changes. An external audit then found safety gaps that review had missed; section 6 lists them and how they were fixed.

## 1. The system

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagram-loop-dark.svg">
  <img alt="The closed loop: 1 AutoLab's optimiser proposes temperature, light and pH; 2 three reactors hold the conditions with their own control and safety; 3 QC checks every replicate, 2 of 3 must pass; 4 the optimiser learns. Measured data also flows to the data pipeline, locally or on Azure." src="images/diagram-loop.svg">
</picture>

**The rules that shape it:**
- **Truth, measurement, decision and actuation are kept apart.** Only the simulator sees true temperature and biomass; everything else, including AutoLab, sees noisy sensor readings.
- **The device owns safety.** It refuses out-of-range commands itself, trips on over-temperature or low pH, and keeps holding safely if AutoLab or the network disappears. Trips latch, an independent cutout backs up the thermostat's probe, and a watchdog ends any hold at 96 h.
- **AutoLab has no simulator-specific code.** The device plugs in through AutoLab's own controller and sensor contracts, as real hardware would. AutoLab did evolve during the project, gaining culture contract v2 for pH and a grid option for the benchmark, but as general features.

## 2. One experiment

- **The steps:** the runner checks each proposal against its policy and the limits the device declares. It sends one setpoint command per replicate reactor, and the device acknowledges the values it actually applied. Then AutoLab polls telemetry and OD750 every 15 minutes of simulated time for the 36-hour hold.
- **Time:** a 36-hour culture runs in a fraction of a second on simulated time, so a 20-experiment campaign takes seconds.
- **Faults:** heater failures, culture crashes and stuck CO₂ valves can be injected into single replicates. AutoLab's QC catches each one; the [README](../README.md) lists them.
- **Reproducibility:** every run is seeded, and rerunning a campaign gives byte-identical evidence and charts.

## 3. Results

### Does the optimiser beat simpler strategies?

90 campaigns: AutoLab's optimiser, random search and a 3 × 3 × 2 grid, 30 paired runs each, 18 experiments per run. Run *k* gives every method the same simulated world, so differences come from the strategy, not from luckier noise. Full method and limits are in [benchmark.md](benchmark.md).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/benchmark-progress-dark.png">
  <img alt="Median best-so-far trade-off coverage and best OD750 for the optimiser, random search and grid over 18 experiments" src="images/benchmark-progress.png">
</picture>

| Optimiser vs | Better trade-offs (hypervolume) | Denser cultures (best OD750) |
|---|---|---|
| Random search | **+2.4 %**, better in 24 of 30 runs, p = 0.001 | no better; slightly worse if anything (−1.5 %, p = 0.12) |
| Grid | **+12.7 %**, better in 30 of 30, p < 0.001 | +3.0 %, p < 0.001 |

- **What the optimiser learns:** to fill the cheap, thin corner of the outcome space, which the hypervolume score rewards. Count only cultures above OD 0.8 and the edge over random search is no longer significant. At OD ≥ 1.0 its cheapest culture is no cheaper than random search's (about 2.4 kWh and 5–5.5 g CO₂ for both), and at OD ≥ 1.2 it used more CO₂. It doesn't find denser cultures either.
- **What I'd change:** give the objective a minimum useful density, so the score stops rewarding thin cultures.
- **The size of the edge:** small, because on this smooth simulated surface random search is a strong baseline.
- **Robustness:**
  - The result held when the growth model changed underneath it (before shading: +2.5 %).
  - It held when AutoLab's seed scheme changed (previously +2.9 %).
  - With fewer random start-up experiments the edge grew to +3.3 %. That setting wasn't chosen in advance, so it isn't the headline.

### Does running experiments in parallel cost anything?

AutoLab's stage 8 runs several conditions at once, each on its own group of reactors. On a 9-reactor bank, batches of 3 against one at a time:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/benchmark-batch-dark.png">
  <img alt="Batches of 3 reach the one-at-a-time result in 6 rounds of culture instead of 18" src="images/benchmark-batch.png">
</picture>

- **Same result, a third of the time.** With the same 18-experiment budget, batching ends at the same trade-off: the 95 % interval runs from 1.0 % worse to 1.6 % better than one at a time. It gets there in 6 rounds of 36-hour culture instead of 18. The price is three times the reactors.
- **The original results didn't move.** On the stage 8 AutoLab, the original 90 campaigns reproduced byte for byte, so the one-at-a-time behaviour is unchanged.

### Is the growth model realistic?

- **Grounding:** parameters come from published *C. vulgaris* papers, read in full where possible, each cited with a confidence level ([model.md](model.md)).
- **The check:** the model was tested against an independent experiment (Pinto et al. 2026, 12 batch runs, 9 distinct conditions) with pass rules written before the first run ([validation.md](validation.md)).

| | First check | After adding light self-shading | A constant growth rate, no biology |
|---|---|---|---|
| Runs within ±30 % on early growth | 0 of 12 | 9 of 12 (at an assumed 5 cm light path) | **11 of 12** |
| Typical growth rate, model ÷ measured | 2.1× (4.7× at the start of growth) | 1.05× | n/a |
| Temperature ranking | backwards | still backwards: this strain prefers cooler conditions than the pooled literature values | n/a |

- **The pass rule turned out too weak to mean much.** A constant rate beats the model, so passing says little about the biology. The temperature ranking is the informative part, and it fails.
- **The shading fix is a calibration, not an independent check.** Its light path (5 cm) is unreported in the paper and was chosen while fixing the failure, and the pass count swings from 0 to 10 of 12 across 2–13 cm. The rerun used the same dataset.
- The failed first check was documented, not tuned away, and what still doesn't fit is stated plainly.

### Does the data pipeline hold up?

| Check | Local | Azure |
|---|---|---|
| Messages from a 15-experiment campaign | about 10,000 | 10,035 lines, 22 batches, about 32 s |
| Bad lines quarantined with a reason | 3 | 3 (old schema, torn write, an impossible 412 °C) |
| Full replay of the log | 0 new rows | 20,264 rows stored, **10,032 unique** kept by the dedupe views |
| Per-hold report | matches AutoLab's QC verdicts | **identical to the local report on all 45 holds** |

## 4. The Azure setup

Built in the Azure portal by me, at **£0**: the free account's hard spending limit, plus the free Data Explorer cluster.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagram-azure-dark.svg">
  <img alt="Azure setup: Microsoft Entra ID signs in the laptop, with no keys anywhere. The laptop uploader sends raw batches to Blob Storage (role: blob read and write) and clean rows to Data Explorer (role: ingest and read only), where tables feed dedupe views and the report. Cost £0." src="images/diagram-azure.svg">
</picture>

**The data, queried in the cloud.** `HoldSummary()` charted directly in Data Explorer: final OD750 for every reactor in every experiment of the demo campaign. This chart is from the campaign run on 2026-11-14. AutoLab's seed scheme has changed since then, so the heater faults now sit in experiments 1 and 4 instead of 5 and 6. The injected faults show up as the odd bar out:
- experiment 5, reactor-2: heater failure;
- experiment 9, reactor-3: culture crash;
- experiment 11, reactor-1: low-pH trip after a stuck CO₂ valve.

Experiment 6's heater faults lowered reactors 1 and 2 only slightly, but QC still caught them on temperature tracking.

![Column chart from Azure Data Explorer: final OD750 per experiment (0 to 14) for reactors 1, 2 and 3. Most triplets are close together; experiment 9 reactor-3 is near zero (culture crash), experiment 11 reactor-1 is low (pH trip), and experiment 5 reactor-2 is lower (heater failure).](images/azure-data-explorer-chart.png)

**Decisions, and what building it taught me:**
- **No Azure Function.** The free trial refuses the Flex Consumption plan, even though the docs imply otherwise, and the older plan is retired for Python. So the laptop uploader does the Function's job, and the parse rules are shared code, not a copy.
- **Queued, not streaming, ingestion.** The free cluster refused streaming writes (403) even with the right role. I diagnosed it by probing each write path with the same identity: reads, inline ingestion and queued ingestion worked, and streaming didn't.
- **Two identities, least privilege where Azure allows it.** The Azure sign-in is a different principal from the Microsoft account that created the cluster, so it got explicit *Ingestor* and *Viewer* rights, nothing more. On storage, the smallest built-in role that can write blobs (*Storage Blob Data Contributor*) can also delete them, so true write-only would need a custom role or immutable storage. Permission errors stop the uploader with the identity named, instead of retrying quietly.
- **Duplicates are expected, then removed.** Data Explorer accepts everything; materialised views keep the first copy of each message, the same rule as the local store, so resends and replays are harmless.
- **What it can't do yet:** sign-in is an interactive browser login, so it can't run unattended, and queued ingestion is never checked for completion; a missing batch would only show up in `PipelineHealth()`.

## 5. What I'd do next

- **A real reactor.** A jar of *Chlorella*, a heater, an LED panel and cheap sensors on a Raspberry Pi, behind the same device contract, so AutoLab wouldn't change at all. That's the one step that would turn "simulated" into "real".
- **A strain-specific temperature curve** and a nitrogen limit, each checked against a dataset it wasn't fitted to.

## 6. What an external audit found, and what changed

An audit written as if by a lab-automation lead (2026-11-16) found real gaps. Each fix below has a test that reproduces the original failure.

| Found | Fixed |
|---|---|
| A second client could replace a running hold or restart a reactor someone else had made safe | Holds have an owner; anyone may make a reactor safe, but only its owner restarts it |
| A reused command ID with a different reactor was answered "duplicate, accepted", and that reactor never started | The device fingerprints each request; a mismatched reuse is refused |
| The "latching" over-temperature trip reset itself 2 °C below the trip; the low-pH shutoff reopened, so a stuck valve cycled through 6 trips | Trips latch; the over-temperature trip needs an operator release |
| Protection relied on one probe: reading 4 °C low, the vessel reached 40 °C with no trip | An independent hardware cutout, modelled on a manual-reset thermostat, that doesn't use the probe; a heater that fails stuck on is now simulated too |
| The declared 96 h maximum hold wasn't enforced, and one unbounded clock request could stall the device | A watchdog ends holds at 96 h; clock requests are bounded |
| After the device lost its state, new data reused sequence numbers and was silently dropped as "duplicates" | Numbering continues past the capture log; a "duplicate" with different data is quarantined |
| The uploader moved its cursor before reading the reply, and missed a replaced log | It acknowledges only a well-formed reply, and recognises a replaced log |
| Local and Azure kept different copies of a duplicate | Both keep the first |
| Validation and benchmark claims overstated (above) | Corrected on this page and in [validation.md](validation.md) and [benchmark.md](benchmark.md) |

AutoLab's own findings, including a safe-state sweep that one faulty reactor could abort, are being fixed in the AutoLab repository.

## Limits, stated plainly

- **Every result except the validation comes from a simulator.** Nothing here predicts real algae behaviour.
- **The benchmark is narrow:** one strain, one budget and one search space. Real hardware adds drift and failures that could erase a 3 % edge.
- **Every safety claim is tested only against simulated faults.** Real hardware fails in ways a simulator doesn't think of.
- **The Azure setup is a demonstration at demo scale:** one user, a free cluster with no SLA, and no production hardening.
