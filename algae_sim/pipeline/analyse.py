"""Analyse the clean tables: one summary per culture hold, plus pipeline health.

    python -m algae_sim pipeline report --data runs/pipeline

This works only from what the pipeline stored, independently of AutoLab's own QC.
That independence is the point: if both views flag the same replicates (a heater
failure, a culture crash), the data path is trustworthy end to end.

A *hold* is one accepted setpoint command on one reactor, identified by its command
ID. Environment samples taken after safe state (heater and lights off) still belong
to the hold but are excluded from control statistics.

Flag thresholds mirror the demo campaign's QC settings and are illustrative.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path

WARMUP_H = 3.0
MAX_MEAN_DEVIATION_C = 1.0
MAX_MISSING_FRACTION = 0.2
MAX_OD_DROP = 0.05
EXPECTED_SAMPLE_GAP_H = 10 / 60


@dataclass(slots=True)
class HoldSummary:
    device_id: str
    experiment_id: str
    command_id: str
    hold_number: int | None
    setpoint_c: float
    light_umol_m2_s: float
    hours_held: float
    samples: int
    missing_temperature: int
    mean_abs_deviation_c: float | None
    max_temperature_c: float | None
    energy_kwh: float
    od_readings: int
    od_first: float | None
    od_last: float | None
    growth_rate_per_h: float | None
    largest_od_drop: float
    longest_capture_gap_h: float
    faults: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def hold_summaries(db: sqlite3.Connection) -> list[HoldSummary]:
    env: dict[tuple[str, str], list[tuple]] = {}
    for row in db.execute(
        "select device_id, command_id, experiment_id, hold_number, hold_elapsed_h, "
        "temperature_c, setpoint_c, light_umol_m2_s, tripped, safe_state, energy_kwh, "
        "faults, timestamp from environment order by device_id, sequence"
    ):
        env.setdefault((row[0], row[1]), []).append(row)
    od: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for device_id, command_id, elapsed, value in db.execute(
        "select device_id, command_id, hold_elapsed_h, od750 from od_readings "
        "order by device_id, sequence"
    ):
        od.setdefault((device_id, command_id), []).append((elapsed, value))

    summaries = []
    for (device_id, command_id), rows in env.items():
        active = [r for r in rows if not r[9]]
        if not active:
            continue
        temps = [r[5] for r in active if r[5] is not None]
        settled = [abs(r[5] - r[6]) for r in active if r[5] is not None and r[4] >= WARMUP_H]
        times = [datetime.fromisoformat(r[12]) for r in rows]
        gaps = [(b - a).total_seconds() / 3600 for a, b in pairwise(times)]
        faults = sorted({f for r in active for f in json.loads(r[11])})
        readings = od.get((device_id, command_id), [])
        drops = [a[1] - b[1] for a, b in pairwise(readings)]
        s = HoldSummary(
            device_id=device_id,
            experiment_id=rows[0][2],
            command_id=command_id,
            hold_number=rows[0][3],
            setpoint_c=active[0][6],
            light_umol_m2_s=active[0][7],
            hours_held=active[-1][4],
            samples=len(active),
            missing_temperature=len(active) - len(temps),
            mean_abs_deviation_c=_mean(settled),
            max_temperature_c=max(temps) if temps else None,
            energy_kwh=max(r[10] for r in active),
            od_readings=len(readings),
            od_first=readings[0][1] if readings else None,
            od_last=readings[-1][1] if readings else None,
            growth_rate_per_h=_growth_rate(readings),
            largest_od_drop=max([0.0, *drops]),
            longest_capture_gap_h=max(gaps, default=0.0),
            faults=faults,
        )
        s.flags = _flags(s, any(r[8] for r in active))
        summaries.append(s)
    summaries.sort(key=lambda s: (s.hold_number if s.hold_number is not None else -1, s.device_id))
    return summaries


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _growth_rate(readings: list[tuple[float, float]]) -> float | None:
    """Average specific growth rate, ln(OD_last / OD_first) / hours between them."""
    if len(readings) < 2:
        return None
    (t0, od0), (t1, od1) = readings[0], readings[-1]
    if od0 <= 0 or od1 <= 0 or t1 <= t0:
        return None
    return math.log(od1 / od0) / (t1 - t0)


def _flags(s: HoldSummary, tripped: bool) -> list[str]:
    flags = []
    if s.mean_abs_deviation_c is not None and s.mean_abs_deviation_c > MAX_MEAN_DEVIATION_C:
        flags.append("temperature_off_setpoint")
    if tripped:
        flags.append("protection_trip")
    if s.samples and s.missing_temperature / s.samples > MAX_MISSING_FRACTION:
        flags.append("temperature_dropouts")
    if s.largest_od_drop > MAX_OD_DROP:
        flags.append("od_drop")
    if s.longest_capture_gap_h > EXPECTED_SAMPLE_GAP_H * 1.5:
        flags.append("capture_gap")
    return flags


def pipeline_health(db: sqlite3.Connection) -> dict[str, object]:
    batches, lines, stored, duplicates, rejected = db.execute(
        "select count(*), coalesce(sum(lines), 0), coalesce(sum(stored), 0), "
        "coalesce(sum(duplicates), 0), coalesce(sum(rejected), 0) from batches"
    ).fetchone()
    reasons = dict(db.execute(
        "select reason, count(*) from rejected group by reason order by count(*) desc"
    ).fetchall())
    kinds = dict(db.execute("select kind, count(*) from message_keys group by kind").fetchall())
    devices = [d for (d,) in db.execute(
        "select distinct device_id from message_keys order by device_id")]
    return {
        "batches": batches,
        "lines_received": lines,
        "messages_stored": stored,
        "duplicates_dropped": duplicates,
        "lines_rejected": rejected,
        "rejection_reasons": reasons,
        "stored_by_kind": kinds,
        "devices": devices,
    }


def report(data_dir: Path) -> dict[str, object]:
    db = sqlite3.connect(Path(data_dir) / "pipeline.sqlite3")
    try:
        return {
            "pipeline": pipeline_health(db),
            "holds": [asdict(s) for s in hold_summaries(db)],
        }
    finally:
        db.close()


def format_report(data: dict[str, object]) -> str:
    p = data["pipeline"]
    out = [
        "Pipeline health",
        f"  {p['batches']} batches, {p['lines_received']} lines received: "
        f"{p['messages_stored']} stored, {p['duplicates_dropped']} duplicates dropped, "
        f"{p['lines_rejected']} rejected",
    ]
    if p["rejection_reasons"]:
        out.append("  rejected because: " + ", ".join(
            f"{k} x{v}" for k, v in p["rejection_reasons"].items()))
    out.append(f"  stored by kind: {p['stored_by_kind']}")
    out += [
        "",
        "Culture holds (from stored data only)",
        "  hold  reactor    setpoint  light  dev°C  kWh    OD start→end  µ/h     flags",
    ]
    for h in data["holds"]:
        dev = "  -  " if h["mean_abs_deviation_c"] is None else f"{h['mean_abs_deviation_c']:5.2f}"
        od = ("     -     " if h["od_first"] is None
              else f"{h['od_first']:.3f}→{h['od_last']:.3f}")
        mu = "   -  " if h["growth_rate_per_h"] is None else f"{h['growth_rate_per_h']:.3f}"
        flags = ", ".join(h["flags"]) or "ok"
        out.append(
            f"  {h['hold_number'] if h['hold_number'] is not None else '?':>4}  "
            f"{h['device_id'].rsplit('/', 1)[-1]:9s}  {h['setpoint_c']:6.1f}  "
            f"{h['light_umol_m2_s']:6.0f}  {dev}  {h['energy_kwh']:.3f}  {od}  {mu}  {flags}"
        )
    return "\n".join(out)
