# Instrument data pipeline

[Project overview](../README.md) · [Architecture](architecture.md) · [Progress](progress.md)

**Status: the local version is built and tested (`scripts/pipeline_demo.sh`). The Azure version isn't started, and no Azure resources exist.** Steven wants to be closely involved in the Azure setup. Before building it, check the current official Azure docs for each service and agree the cost.

## Purpose

To show the data engineering a closed-loop lab depends on:
- instrument data **captured** at the device;
- **moved** reliably;
- **parsed and validated**;
- **stored** durably;
- **analysed**.

It's the same shape as Steven's 2018/19 Weather Machine project, applied to lab instruments. It runs beside the AutoLab loop, not inside it: the device and AutoLab both work with the pipeline down.

## Shape (local version)

```text
device service ──append──▶ capture log ──▶ uploader ──HTTP──▶ ingest ──▶ raw/ (as received)
 (--capture DIR)          telemetry.jsonl   cursor, retry,     parse,     └─▶ SQLite clean tables
                          (the buffer)      backoff            validate,       └─▶ report
                                                               dedupe
```

| Stage | Module | What it guarantees |
|---|---|---|
| Capture | `device/core.py`, `device/service.py` | An environment sample every 10 device-minutes per reactor, an extra one the moment a protection trip fires, and an event for every OD reading. One sequence counter per reactor. The log is written and fsynced *before* the state file, so a crash can repeat messages but never lose them. On restart, a torn last line is ended cleanly. |
| Move | `pipeline/uploader.py` | A separate process. The log is the buffer, and a byte-offset cursor records what has been acknowledged. The cursor advances only after a 200 reply (at-least-once delivery). Only complete lines are sent. While the ingest is unreachable it backs off, doubling the wait up to 8 s. |
| Parse/validate | `pipeline/parse.py` | Pure functions. Each line becomes one clean row or a named rejection: invalid JSON, wrong schema version, a missing or invalid field, a timestamp with no timezone, an implausible value, or an unknown kind. `simulator_truth` is dropped here. |
| Store | `pipeline/store.py`, `pipeline/ingest.py` | The raw batch is kept byte-for-byte before parsing. Each batch lands in one SQLite transaction. `(device_id, sequence)` is the dedupe key, so resends and replays are counted, not stored twice. Rejected lines are kept with their reason. |
| Analyse | `pipeline/analyse.py` | Per hold, from stored data only: temperature deviation after warm-up, dropouts, trips, energy, OD start→end, growth rate, largest OD drop and capture gaps. Also pipeline health: batches, duplicates and rejections by reason. |

Commands:

```sh
uv run python -m algae_sim device serve --capture runs/device/capture ...
uv run python -m algae_sim pipeline ingest --data runs/pipeline
uv run python -m algae_sim pipeline upload --capture runs/device/capture --follow
uv run python -m algae_sim pipeline report --data runs/pipeline
```

Message format, schema version 3: `kind` (`environment` | `od`), `device_id` (`<bank>/<reactor>`), `sequence`, `timestamp` (device time, UTC), `context` (command ID, experiment ID, hold number, hours into the hold), `measurements` (temperature and pH for environment samples), plus `control` (setpoints, heater and CO2 valve state), `energy_kwh`, `co2_g` and `faults` for environment samples. Version 3 added pH and CO2 with culture contract v2; the parser accepts only the current version. `simulator_truth` is ground truth for tests only.

## Evidence (demo run, 15 experiments × 3 reactors)

- About 10,000 messages captured (10,031 in the latest run, after the contract v2 migration).
- The uploader started while the ingest was down, buffered about 8,000 lines (the exact number depends on timing) over several failed attempts, then drained them once the ingest came up.
- A full replay stored 0 new rows and counted every message as a duplicate.
- Three bad lines were quarantined: an old schema version, a torn write, and an impossible 412 °C reading.
- The pipeline's own analysis flagged exactly the replicates AutoLab's QC failed: hold 5 reactor-2 and hold 6 reactors 1 and 2 (heater faults, off setpoint), hold 9 reactor-3 (culture crash, OD drop) and hold 11 reactor-1 (CO2 valve stuck open: `protection_trip`, `ph_off_setpoint`). These are two independent views of the same events.

## Azure version (proposed 2026-11-14, nothing created yet)

### Facts checked against official Microsoft pages, 2026-11-14

| Service | What matters here | Source |
|---|---|---|
| Free account | $200 credit for 30 days, card needed. A **spending limit is on by default**: at the limit, or after 30 days, services are disabled, not charged, unless you upgrade. Free monthly amounts continue on pay-as-you-go. | learn.microsoft.com, *avoid charges with your free account* (updated 2026-03-25); *spending limit* (2026-04-30) |
| Functions, Flex Consumption | 250,000 executions and 100,000 GB-s a month, per subscription. No minimum charge with always-ready set to 0. Python 3.10–3.15. Linux on the legacy Consumption plan is retired for new features, so Flex is the right plan. UK availability is checked with `az functionapp list-flexconsumption-locations`. | *Flex Consumption plan* (2026-11-09); *Functions scale* (2026-11-08) |
| Data Explorer free cluster | **No subscription or card needed.** About 100 GB, 1 year, may be renewed or withdrawn by Microsoft at any time, no SLA. Data sits in "Europe", not a chosen UK region. Supports streaming ingestion (4 MB per request), the Python SDKs, materialised views (for dedupe) and dashboards. | *Start for free* (2026-06-11); *streaming ingestion* (2026-02-11) |
| Azure SQL free offer (fallback) | 100,000 vCore-s and 32 GB a month per database, up to 10. Choose **AutoPause** at the limit; BillOverUsage can't be undone. An open connection stops it pausing. | *Free offer* (2026-11-11) |
| Blob Storage | Hot LRS in UK South: about £0.014 per GB-month, £0.044 per 10,000 writes. 5 GB free for 12 months on the free account. A few MB costs effectively nothing. | Free-services page; Microsoft retail prices API |
| Budgets | **Only alert** (with an 8–24 h lag); they stop nothing unless an action group runs something you wrote. There is no spending limit on pay-as-you-go. | *Create budgets* (2025-09-26) |
| IoT Hub Free | 8,000 messages a day, metered in 0.5 KB units. Not needed for this design. | IoT Hub scaling (2026-05-06) |

### Proposed design

```text
laptop: device service → capture log → uploader ──HTTPS──▶ Azure Function (Flex Consumption, UK South)
                                                             ├─▶ Blob container raw/  (batch exactly as received)
                                                             └─▶ parse.py (unchanged) ─▶ Data Explorer free cluster
                                                                                          ├─ environment, od, rejected tables
                                                                                          ├─ materialised views: dedupe on (device_id, sequence)
                                                                                          └─ dashboard: the per-hold report as KQL
```

- **Same rules, new hosting.** `parse.py` and the message format don't change. The Function does what `ingest.py` does today, and the uploader only needs a URL and a key.
- **Dedupe moves to the store.** Data Explorer accepts duplicates on ingest; a materialised view with `arg_max(ingested_at, *) by device_id, sequence` keeps one row per message, so replays stay harmless.
- **Code layout.** Azure code goes in its own optional subpackage (`algae_sim/azure/`, with its own extra dependencies), so the core package stays standard-library only.
- **Secrets.** The Function key and connection settings live in environment variables and `az login`, never in Git. The Function reaches storage and Data Explorer by managed identity where supported.
- **Cost guards:**
  - build inside the free account's 30 days, where the spending limit is a real hard cap;
  - tag every resource and keep everything in one resource group;
  - a one-command teardown that deletes that group;
  - a £5 budget alert if it stays on pay-as-you-go;
  - Application Insights off or heavily sampled, since its cost wasn't checked.
- **Expected cost:** £0 during the free month. On pay-as-you-go afterwards, pennies a month for blob storage, with Functions inside the free grant.

### Change: no Function for now (2026-11-15)

Creating the Function App failed: the portal says *"Free trial subscription is not supported for Flex Consumption"*. Microsoft's docs imply it is supported (they list a 15-core quota for free trials); the portal decides. Python on the legacy Consumption plan needs Linux, which is retired for new features, so it's no fallback.

**Decision (Steven):** drop the Function and keep £0 with the hard cap. The uploader on the laptop does the Function's job:
- it writes each raw batch to the `raw` container;
- it parses with the same `parse.py`;
- it ingests clean rows into Data Explorer.

The laptop signs in to both services as Steven, through a browser login with Entra ID, so no keys or connection strings are needed. The Function can be added later, after an upgrade to pay-as-you-go, without changing anything else.

```text
laptop: device service → capture log → uploader ──┬─▶ Blob: stalgaesim1107/raw  (batch as received)
                                                  └─▶ parse.py ─▶ Data Explorer free cluster (dedupe view, dashboard)
```

Created so far (UK South, tag `project=algae-sim`): resource group `rg-algae-sim-pipeline`, storage account `stalgaesim1107`, private container `raw`.

### Decisions (Steven, 2026-11-14)

- **Account:** a new Azure free account. Build inside its 30 days, where the spending limit is a hard cap; afterwards, tear down or keep only free-tier pieces.
- **Clean store:** the Data Explorer free cluster.
- **Cost ceiling: £0.** No pay-as-you-go spend without a new decision.
- **Region:** UK South for the Function and storage (to confirm with `az functionapp list-flexconsumption-locations`).

Data Explorer's free cluster needs no subscription, so a £0, no-card variant is possible: the uploader writes straight to Data Explorer, with raw batches kept in a table. It drops the Function and Blob, and so most of the cloud-engineering practice.
