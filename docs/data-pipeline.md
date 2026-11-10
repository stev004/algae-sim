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
| Capture | `device/core.py`, `device/service.py` | An environment sample every 10 device-minutes per reactor, and an event for every OD reading. One sequence counter per reactor. The log is written and fsynced *before* the state file, so a crash can repeat messages but never lose them. On restart, a torn last line is ended cleanly. |
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

Message format, schema version 2: `kind` (`environment` | `od`), `device_id` (`<bank>/<reactor>`), `sequence`, `timestamp` (device time, UTC), `context` (command ID, experiment ID, hold number, hours into the hold), `measurements`, plus `control`, `energy_kwh` and `faults` for environment samples. `simulator_truth` is ground truth for tests only.

## Evidence (demo run, 15 experiments × 3 reactors)

- 10,035 messages captured.
- The uploader started while the ingest was down, buffered about 8,000 lines (the exact number depends on timing) over several failed attempts, then drained them in 21 batches once the ingest came up.
- A full replay stored 0 new rows and counted 10,035 duplicates.
- Three bad lines were quarantined: an old schema version, a torn write, and an impossible 412 °C reading.
- The pipeline's own analysis flagged exactly the replicates AutoLab's QC failed: hold 5 reactor-2 and hold 14 reactors 1 and 2 (heater faults, off setpoint), and hold 9 reactor-3 (culture crash, OD drop). These are two independent views of the same events.

## Azure version (planned)

Each local piece has an Azure counterpart. The parse rules and message format stay as they are.

| Local | Azure candidate | Free allowance, checked 2026-11-10 (check again before building) |
|---|---|---|
| ingest HTTP server | Azure Functions, HTTP trigger, Flex Consumption plan | 250,000 executions and 100,000 GB-s a month, per subscription |
| `raw/` folder | Blob Storage container | Not free; a few MB costs cents a month |
| SQLite clean tables | Azure Data Explorer free cluster (alternative: Azure SQL Database free tier) | ADX: no subscription or card needed, ~100 GB, 1 year, renewable. SQL: 100,000 vCore-s and 32 GB a month, can auto-pause at the limit |
| report | KQL queries plus an ADX dashboard | Included in the free cluster |
| (later) device identity | IoT Hub Free | 8,000 messages a day and 500 devices. Too few for one message per sample at campaign speed, so it would need batched messages or slower capture |

Open decisions for Steven:
- Account: new free account ($200 for 30 days, card required; services are disabled rather than charged unless you upgrade) or pay-as-you-go with free tiers.
- Region.
- Data Explorer or SQL as the clean store.
- Whether to add IoT Hub.

Budgets only send alerts; they don't stop spending. So also plan:
- free-tier settings that cap or pause usage;
- a small budget alert;
- a one-command teardown.

Data Explorer doesn't deduplicate on ingest, so the `(device_id, sequence)` rule would move to a materialised view (`arg_max`) or be applied at query time.

Secrets (the Function key and storage connection strings) stay out of Git, in environment variables or `az login`.
