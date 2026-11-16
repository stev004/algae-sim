# Instrument data pipeline

[Project overview](../README.md) · [Architecture](architecture.md) · [Progress](progress.md)

**Status: the local version and the Azure version both work.** On 2026-11-15 the Azure version took the full 10,035-message demo capture into Blob Storage and Azure Data Explorer, and its per-hold report matched the local pipeline exactly. It costs £0: it runs on the free account with its hard spending limit, plus the free Data Explorer cluster. Steven set it up himself in the portal.

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

## Azure version (built 2026-11-15)

### Evidence (first full run, 2026-11-15)

- **Raw:** each batch is stored byte for byte in Blob Storage under a content-hash name. The first 200-line test batch matched the source file's size exactly, and a failed earlier attempt overwrote the same blob instead of adding a copy.
- **Full capture:** 10,035 lines from the 15-experiment demo were uploaded in 22 batches in about 32 s: 10,032 stored and 3 rejected (old schema, torn write, impossible 412 °C reading).
- **Replay:** the whole log was sent again. Data Explorer stored every copy (20,264 rows), and the `*Clean` materialised views kept exactly **10,032 unique messages**. `PipelineHealth()` reported 10,232 duplicates removed.
- **Report:** `HoldSummary()` in Data Explorer and the local pipeline's report agree for all **45 holds**. Energy, CO₂, OD start and end, sample counts, temperature deviation and growth rate are identical.

### How to run it

```sh
uv sync --extra azure
uv run python -m algae_sim pipeline upload --capture <capture dir> --azure configs/azure.local.json
```

- **Config:** copy `configs/azure.example.json` to `configs/azure.local.json` (git-ignored) and fill in the tenant ID, storage account and cluster URI. They're addresses, not secrets.
- **Sign-in:** the first run opens a browser sign-in; it's then cached in the macOS keychain.
- **Tables and views:** `algae_sim/azure/schema.kql` creates them. It's generated by `python -m algae_sim.azure schema`, and a test keeps it in step with the code.
- **Permissions needed** (least privilege):
  - *Storage Blob Data Contributor* on the storage account. That's the smallest built-in role that can write blobs, but it can also delete them. True write-only would need a custom role, or immutable storage on the container;
  - *Ingestor* and *Viewer* on the `algae` database. Grant them to the Entra ID user (`aaduser=<object id>;<tenant id>`), which is not the same identity as the Microsoft account that created the free cluster.

### What we learnt while building it

- **The free trial can't host a Flex Consumption Function,** even though the docs imply it can (see below). The laptop uploader does the Function's job instead.
- **Streaming ingestion is refused (403)** on the free cluster for an Entra ID user, even with the Ingestor role and the streaming policy on. Reads, inline ingestion and **queued ingestion** all work. The sink uses queued ingestion, with the database's batching window cut to 30 s.
- **Two identities.** Signing in through Azure gives a different principal from the Microsoft account the free cluster was created with, so the cluster needs explicit grants for it. The uploader now stops on 401/403 with the principal named, instead of retrying quietly.

## Azure version: plan and decisions

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
- **Dedupe moves to the store.** Data Explorer accepts duplicates on ingest; a materialised view with `arg_min(ingested_at, *) by device_id, sequence` keeps the first copy of each message, the same rule as the local store, so replays stay harmless. (It used `arg_max`, the latest copy, until 2026-11-16, so the local and cloud stores resolved a conflict in opposite ways. A live cluster needs `schema.kql` re-run to pick the change up.)
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
