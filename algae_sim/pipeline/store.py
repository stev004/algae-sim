"""Where ingested data lives: raw batches as received, and clean deduplicated tables.

Two stores, on purpose:

* ``RawStore`` keeps every batch byte-for-byte as it arrived, in a dated folder.
  It's the audit trail: if a parse rule turns out wrong, the clean tables can be
  rebuilt from it. (Azure version: a Blob Storage container.)
* ``Database`` holds the parsed rows. ``(device_id, sequence)`` identifies a message,
  so a resent or replayed message is counted as a duplicate and stored once.
  Rejected lines are kept with their reason, not thrown away. (Azure version:
  Data Explorer tables.)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from algae_sim.pipeline.parse import EnvironmentRow, OdRow, Row

SCHEMA = """
create table if not exists batches (
    batch_id text primary key,
    received_at text not null,
    raw_path text not null,
    lines integer not null,
    stored integer not null,
    duplicates integer not null,
    rejected integer not null
);
-- one row per message ever stored: the deduplication key
create table if not exists message_keys (
    device_id text not null,
    sequence integer not null,
    kind text not null,
    batch_id text not null,
    primary key (device_id, sequence)
);
create table if not exists environment (
    device_id text not null,
    sequence integer not null,
    timestamp text not null,
    command_id text not null,
    experiment_id text not null,
    hold_number integer,
    hold_elapsed_h real not null,
    temperature_c real,
    setpoint_c real not null,
    light_umol_m2_s real not null,
    heater_command real not null,
    heater_applied real not null,
    tripped integer not null,
    safe_state integer not null,
    energy_kwh real not null,
    faults text not null,
    ph real,
    ph_setpoint real not null,
    co2_valve_open integer not null,
    co2_g real not null,
    primary key (device_id, sequence)
);
create table if not exists od_readings (
    device_id text not null,
    sequence integer not null,
    timestamp text not null,
    command_id text not null,
    experiment_id text not null,
    hold_number integer,
    hold_elapsed_h real not null,
    od750 real not null,
    saturated integer not null,
    primary key (device_id, sequence)
);
create table if not exists rejected (
    batch_id text not null,
    line_number integer not null,
    reason text not null,
    detail text not null,
    raw text not null
);
"""


class RawStore:
    """Batches exactly as received, one file each: ``raw/<date>/<batch_id>.jsonl``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def put(self, batch_id: str, received_at: datetime, body: bytes) -> Path:
        folder = self.root / received_at.date().isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{batch_id}.jsonl"
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, path)
        return path


@dataclass(slots=True)
class BatchOutcome:
    batch_id: str
    lines: int = 0
    stored: int = 0
    duplicates: int = 0
    rejected: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "lines": self.lines,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
        }


class Database:
    """SQLite clean store. One connection, guarded by a lock (the ingest is threaded)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.executescript(SCHEMA)
        self.lock = threading.Lock()

    def close(self) -> None:
        self.connection.close()

    def record_batch(
        self,
        outcome: BatchOutcome,
        rows: list[Row],
        rejections: list[tuple[int, str, str, str]],
        *,
        received_at: datetime,
        raw_path: Path,
    ) -> BatchOutcome:
        """Store one batch in a single transaction: all of it lands, or none of it."""
        with self.lock, self.connection as db:
            for row in rows:
                inserted = db.execute(
                    "insert or ignore into message_keys values (?, ?, ?, ?)",
                    (row.device_id, row.sequence, row.kind, outcome.batch_id),
                ).rowcount
                if not inserted:
                    outcome.duplicates += 1
                    continue
                _insert_row(db, row)
                outcome.stored += 1
            db.executemany(
                "insert into rejected values (?, ?, ?, ?, ?)",
                [(outcome.batch_id, *rejection) for rejection in rejections],
            )
            db.execute(
                "insert into batches values (?, ?, ?, ?, ?, ?, ?)",
                (
                    outcome.batch_id,
                    received_at.isoformat(),
                    str(raw_path),
                    outcome.lines,
                    outcome.stored,
                    outcome.duplicates,
                    len(rejections),
                ),
            )
        return outcome


def _insert_row(db: sqlite3.Connection, row: Row) -> None:
    c = row.context
    key = (row.device_id, row.sequence, row.timestamp, c.command_id, c.experiment_id,
           c.hold_number, c.hold_elapsed_h)
    if isinstance(row, EnvironmentRow):
        db.execute(
            "insert into environment values "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, row.temperature_c, row.setpoint_c, row.light_umol_m2_s,
             row.heater_command, row.heater_applied, int(row.tripped), int(row.safe_state),
             row.energy_kwh, json.dumps(list(row.faults)), row.ph, row.ph_setpoint,
             int(row.co2_valve_open), row.co2_g),
        )
    elif isinstance(row, OdRow):
        db.execute(
            "insert into od_readings values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, row.od750, int(row.saturated)),
        )
