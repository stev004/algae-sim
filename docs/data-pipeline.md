# Azure data pipeline (planned side project)

[Project overview](../README.md) · [Architecture](architecture.md) · [Progress](progress.md)

**Status: design only. Nothing is deployed and no Azure resources exist.** Before building, verify current official Azure documentation for each service and agree the cost with Steven.

## Purpose

To demonstrate the data engineering a closed-loop lab depends on: instrument data **captured** at the device, **moved** reliably, **parsed and validated**, **stored** durably, and **analysed**. This is the same shape as Steven's 2018/19 Weather Machine project, applied to lab instruments. It sits beside the AutoLab loop rather than inside it: local control and AutoLab both keep working without it.

## Shape

```text
reactor runtime (local)          Azure
telemetry.jsonl messages ──▶ ingest ──▶ parse/validate ──▶ store ──▶ analyse/view
 schema_version, device_id,      (device     (reject bad       (raw +     (growth curves,
 sequence, timestamp,            identity)   schema, dedupe    clean      energy, faults)
 measurements, control                       by device+seq)    tables)
```

The capture format already exists: `algae_sim/records.py` writes one JSON message per telemetry sample with a schema version, device ID, monotonically increasing sequence number, ISO-8601 UTC timestamp, measurements and control state. Simulator truth sits in a separate block that the pipeline should drop.

## Questions to settle before building

- Ingest service (e.g. IoT Hub vs Event Hubs vs a plain HTTPS function), storage (e.g. Blob + a SQL or Data Explorer table) and analysis surface. Choose from current docs and pricing, favouring free tiers.
- Per-device identity and secret handling, kept out of Git.
- Duplicate and out-of-order handling, local buffering while offline, and what "delivered" means compared with "confirmed by later measurements".
- Cost ceiling and a teardown command.
