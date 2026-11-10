"""Instrument-data pipeline: capture → move → parse/validate → store → analyse.

This is the local, standard-library version. Each stage is a small module whose
storage sits behind a narrow class, so the Azure version can swap the local folder
and SQLite file for Blob Storage and Data Explorer without changing the parse rules:

* ``parse``    — one message line in, one clean row or a named rejection out (pure);
* ``store``    — raw batches kept exactly as received, plus deduplicated clean tables;
* ``ingest``   — the HTTP endpoint the uploader posts to (stand-in for an Azure Function);
* ``uploader`` — the device-side agent: reads the capture log, buffers while the
  pipeline is unreachable, retries, and only advances its cursor on acknowledgement;
* ``analyse``  — per-hold summaries and pipeline-health numbers from the clean tables.

See docs/data-pipeline.md.
"""
