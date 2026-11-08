"""Run records: what a reactor run captured, in a form other systems can ingest.

``TelemetrySample`` is the instrument-capture message. Its JSON Lines form is the
contract a later cloud pipeline (docs/data-pipeline.md) would parse and store: it
carries only what a device could know (measurements, commands, applied state).
True state is kept in a separate, clearly labelled ``simulator_truth`` block so the
pipeline can ignore it while tests and analysis can still check against it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

TELEMETRY_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    sequence: int
    elapsed_s: float
    measured_temperature_c: float | None
    measured_od750: float
    temperature_setpoint_c: float
    heater_command: float
    heater_applied: float
    light_umol_m2_s: float
    controller_tripped: bool
    true_temperature_c: float
    true_biomass_g_per_l: float

    def to_message(self, device_id: str, start_time: datetime) -> dict[str, Any]:
        return {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "device_id": device_id,
            "sequence": self.sequence,
            "timestamp": (start_time + timedelta(seconds=self.elapsed_s)).isoformat(),
            "measurements": {
                "temperature_c": self.measured_temperature_c,
                "od750": self.measured_od750,
            },
            "control": {
                "temperature_setpoint_c": self.temperature_setpoint_c,
                "heater_command": self.heater_command,
                "heater_applied": self.heater_applied,
                "light_umol_m2_s": self.light_umol_m2_s,
                "tripped": self.controller_tripped,
            },
            "simulator_truth": {
                "temperature_c": self.true_temperature_c,
                "biomass_g_per_l": self.true_biomass_g_per_l,
            },
        }


def write_run(
    directory: Path,
    *,
    device_id: str,
    start_time: datetime,
    config: dict[str, Any],
    summary: dict[str, Any],
    telemetry: list[TelemetrySample],
) -> Path:
    """Write ``run.json`` (config + summary) and ``telemetry.jsonl`` to ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_text(
        json.dumps({"device_id": device_id, "config": config, "summary": summary}, indent=2)
        + "\n"
    )
    with (directory / "telemetry.jsonl").open("w") as handle:
        for sample in telemetry:
            handle.write(json.dumps(sample.to_message(device_id, start_time)) + "\n")
    return directory


def sample_as_dict(sample: TelemetrySample) -> dict[str, Any]:
    return asdict(sample)
