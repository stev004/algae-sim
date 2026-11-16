"""Turn parsed rows into Data Explorer records, and generate the matching schema.

Standard library only, so it's testable without Azure. The column lists below are
the single source of truth: ``schema_kql()`` builds the tables and JSON mappings
from them, and a test checks the committed ``schema.kql`` still matches.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from algae_sim.pipeline.parse import EnvironmentRow, OdRow, Rejected, Row, parse_line

DATABASE_DEFAULT = "algae"

_CONTEXT = [
    ("command_id", "string"),
    ("experiment_id", "string"),
    ("hold_number", "int"),
    ("hold_elapsed_h", "real"),
]
_PROVENANCE = [("batch_id", "string"), ("ingested_at", "datetime")]

# table name -> [(column, KQL type)], in table order.
TABLES: dict[str, list[tuple[str, str]]] = {
    "Environment": [
        ("device_id", "string"),
        ("sequence", "long"),
        ("timestamp", "datetime"),
        *_CONTEXT,
        ("temperature_c", "real"),
        ("setpoint_c", "real"),
        ("light_umol_m2_s", "real"),
        ("heater_command", "real"),
        ("heater_applied", "real"),
        ("tripped", "bool"),
        ("safe_state", "bool"),
        ("energy_kwh", "real"),
        ("faults", "dynamic"),
        ("ph", "real"),
        ("ph_setpoint", "real"),
        ("co2_valve_open", "bool"),
        ("co2_g", "real"),
        *_PROVENANCE,
    ],
    "Od": [
        ("device_id", "string"),
        ("sequence", "long"),
        ("timestamp", "datetime"),
        *_CONTEXT,
        ("od750", "real"),
        ("saturated", "bool"),
        *_PROVENANCE,
    ],
    "Rejected": [
        ("line", "int"),
        ("reason", "string"),
        ("detail", "string"),
        ("text", "string"),
        *_PROVENANCE,
    ],
    "Batches": [
        ("blob_name", "string"),
        ("lines", "int"),
        ("rows", "int"),
        ("rejected", "int"),
        *_PROVENANCE,
    ],
}


def mapping_name(table: str) -> str:
    return f"{table}Mapping"


def batch_id(body: bytes) -> str:
    """Content-addressed: resending the same batch gives the same ID and blob name."""
    return hashlib.sha256(body).hexdigest()[:32]


def blob_name(body: bytes) -> str:
    return f"{batch_id(body)}.ndjson"


def _context(row: Row) -> dict[str, Any]:
    c = row.context
    return {
        "command_id": c.command_id,
        "experiment_id": c.experiment_id,
        "hold_number": c.hold_number,
        "hold_elapsed_h": c.hold_elapsed_h,
    }


def record(row: Row, batch: str, ingested_at: str) -> tuple[str, dict[str, Any]]:
    """(table, record) for one clean row."""
    base = {"device_id": row.device_id, "sequence": row.sequence,
            "timestamp": row.timestamp, **_context(row)}
    provenance = {"batch_id": batch, "ingested_at": ingested_at}
    if isinstance(row, EnvironmentRow):
        return "Environment", {
            **base,
            "temperature_c": row.temperature_c,
            "setpoint_c": row.setpoint_c,
            "light_umol_m2_s": row.light_umol_m2_s,
            "heater_command": row.heater_command,
            "heater_applied": row.heater_applied,
            "tripped": row.tripped,
            "safe_state": row.safe_state,
            "energy_kwh": row.energy_kwh,
            "faults": list(row.faults),
            "ph": row.ph,
            "ph_setpoint": row.ph_setpoint,
            "co2_valve_open": row.co2_valve_open,
            "co2_g": row.co2_g,
            **provenance,
        }
    if isinstance(row, OdRow):
        return "Od", {**base, "od750": row.od750, "saturated": row.saturated, **provenance}
    raise TypeError(f"unknown row type {type(row).__name__}")


def split_batch(body: bytes, ingested_at: str) -> tuple[dict[str, list[dict]], list[dict]]:
    """Parse a batch into {table: records} plus the per-line rejections for the reply.

    The same rules as the local ingest: every non-empty line becomes one clean
    record or one Rejected record with a reason. Nothing is dropped silently.
    """
    batch = batch_id(body)
    tables: dict[str, list[dict]] = {name: [] for name in ("Environment", "Od", "Rejected")}
    rejected: list[dict] = []
    for number, line in enumerate(body.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            table, rec = record(parse_line(line), batch, ingested_at)
            tables[table].append(rec)
        except Rejected as exc:
            rejected.append({"line": number, "reason": exc.reason})
            tables["Rejected"].append({
                "line": number,
                "reason": exc.reason,
                "detail": exc.detail,
                "text": line.decode("utf-8", errors="replace")[:2000],
                "batch_id": batch,
                "ingested_at": ingested_at,
            })
    return tables, rejected


def to_ndjson(records: list[dict]) -> bytes:
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records).encode()


def schema_kql(database: str = DATABASE_DEFAULT) -> str:
    """The Data Explorer setup script: run it once in the web UI's query window."""
    out = [
        "// algae-sim pipeline schema for Azure Data Explorer.",
        "// Generated by `python -m algae_sim.azure schema`; don't edit by hand.",
        "// Run each command in the Data Explorer web UI (select it, then Run).",
        "",
        "// Queued ingestion batches rows before committing them; 30 s instead of the",
        "// default few minutes, so uploads show up quickly. Needs database admin.",
        f".alter database {database} policy ingestionbatching"
        ' \'{"MaximumBatchingTimeSpan": "00:00:30", "MaximumNumberOfItems": 500,'
        ' "MaximumRawDataSizeMB": 1024}\'',
        "",
    ]
    for table, columns in TABLES.items():
        cols = ", ".join(f"{name}: {kind}" for name, kind in columns)
        out += [f".create-merge table {table} ({cols})", ""]
        mapping = [{"column": name, "path": f"$.{name}"} for name, _ in columns]
        out += [
            f".create-or-alter table {table} ingestion json mapping '{mapping_name(table)}'",
            f"'{json.dumps(mapping, separators=(',', ':'))}'",
            "",
        ]
    out += [
        "// One row per message: resends and replays land twice in the raw tables,",
        "// and these views keep the first copy of each (device_id, sequence), the",
        "// same rule as the local SQLite store.",
    ]
    for table in ("Environment", "Od"):
        out += [
            ".create-or-alter materialized-view with (backfill=true)"
            f" {table}Clean on table {table}",
            "{",
            f"    {table} | summarize arg_min(ingested_at, *) by device_id, sequence",
            "}",
            "",
        ]
    out += [_HOLD_SUMMARY, "", _PIPELINE_HEALTH, ""]
    return "\n".join(out)


_HOLD_SUMMARY = """// Per hold (one reactor's culture under one command), from stored data only.
.create-or-alter function
with (folder="report", docstring="Per-hold summary, as the local pipeline report")
HoldSummary() {
    let warmup_h = 3.0;
    let env = EnvironmentClean | where safe_state == false;
    let od = OdClean
        | summarize (first_h, od_start) = arg_min(hold_elapsed_h, od750),
                    (last_h, od_end) = arg_max(hold_elapsed_h, od750),
                    od_readings = count()
          by device_id, command_id;
    env
    | summarize hold_number = take_any(hold_number),
                setpoint_c = take_any(setpoint_c),
                light_umol_m2_s = take_any(light_umol_m2_s),
                ph_setpoint = take_any(ph_setpoint),
                samples = count(),
                mean_abs_deviation_c = avgif(
                    abs(temperature_c - setpoint_c),
                    hold_elapsed_h >= warmup_h and isnotnull(temperature_c)),
                mean_abs_ph_deviation = avgif(abs(ph - ph_setpoint),
                                              hold_elapsed_h >= warmup_h and isnotnull(ph)),
                missing_temperature = countif(isnull(temperature_c)),
                missing_ph = countif(isnull(ph)),
                protection_tripped = max(toint(tripped)) == 1,
                energy_kwh = max(energy_kwh),
                co2_g = max(co2_g)
      by device_id, command_id
    | join kind=leftouter od on device_id, command_id
    | extend growth_rate_per_h = iff(od_start > 0 and od_end > 0 and last_h > first_h,
                                     log(od_end / od_start) / (last_h - first_h), real(null))
    | project-away device_id1, command_id1, first_h, last_h
    | order by hold_number asc, device_id asc
}"""

_PIPELINE_HEALTH = """// Batches, lines, rejections, and how many duplicates the views removed.
.create-or-alter function with (folder="report", docstring="Pipeline health counters")
PipelineHealth() {
    let stored = toscalar(Environment | count) + toscalar(Od | count);
    let unique = toscalar(EnvironmentClean | count) + toscalar(OdClean | count);
    Batches
    | summarize batches = dcount(batch_id), lines = sum(lines), rejected = sum(rejected)
    | extend rows_stored = stored, unique_messages = unique, duplicates_removed = stored - unique
}"""
