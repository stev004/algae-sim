"""Parse and validate one captured message. Pure functions, no I/O.

The parser is the pipeline's gatekeeper. Every line either becomes one clean row or
is rejected with a short machine-readable reason, so nothing malformed reaches the
tables and nothing is silently dropped. Rules:

* only schema version "2" (the device capture format) is accepted;
* identity (device ID + sequence) and a timezone-aware timestamp are required;
* values must be the right type and physically plausible for this instrument;
* ``simulator_truth`` is discarded here: a real device could never report it, so it
  must not leak into analysis.

The plausibility ranges are deliberately wide. They catch corrupted or misrouted
data, not process deviations; judging the culture is the analysis step's job.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SUPPORTED_SCHEMA_VERSIONS = ("2",)
TEMPERATURE_RANGE_C = (-20.0, 100.0)
OD750_RANGE = (0.0, 10.0)


class Rejected(Exception):
    """A message that fails validation. ``reason`` is a stable code for counting."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Context:
    command_id: str
    experiment_id: str
    hold_number: int | None
    hold_elapsed_h: float


@dataclass(frozen=True, slots=True)
class EnvironmentRow:
    device_id: str
    sequence: int
    timestamp: str  # ISO-8601, normalised to UTC
    context: Context
    temperature_c: float | None  # None = the probe gave no reading (dropout)
    setpoint_c: float
    light_umol_m2_s: float
    heater_command: float
    heater_applied: float
    tripped: bool
    safe_state: bool
    energy_kwh: float
    faults: tuple[str, ...]

    kind = "environment"


@dataclass(frozen=True, slots=True)
class OdRow:
    device_id: str
    sequence: int
    timestamp: str
    context: Context
    od750: float
    saturated: bool

    kind = "od"


Row = EnvironmentRow | OdRow


def parse_line(line: str | bytes) -> Row:
    """Turn one JSON line into a clean row, or raise ``Rejected``."""
    try:
        message = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Rejected("invalid_json", str(exc)) from exc
    if not isinstance(message, dict):
        raise Rejected("not_an_object")
    return parse_message(message)


def parse_message(message: dict[str, Any]) -> Row:
    version = message.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise Rejected("unsupported_schema_version", repr(version))
    device_id = _text(message, "device_id")
    sequence = message.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise Rejected("invalid_sequence", repr(sequence))
    timestamp = _timestamp(message.get("timestamp"))
    context = _context(message.get("context"))
    measurements = _object(message, "measurements")
    kind = message.get("kind")
    if kind == "environment":
        control = _object(message, "control")
        faults = message.get("faults", [])
        if not isinstance(faults, list) or not all(isinstance(f, str) for f in faults):
            raise Rejected("invalid_field", "faults")
        temperature = measurements.get("temperature_c")
        return EnvironmentRow(
            device_id=device_id,
            sequence=sequence,
            timestamp=timestamp,
            context=context,
            temperature_c=(
                None if temperature is None
                else _number(temperature, "temperature_c", TEMPERATURE_RANGE_C)
            ),
            setpoint_c=_number(
                control.get("temperature_setpoint_c"), "temperature_setpoint_c",
                TEMPERATURE_RANGE_C,
            ),
            light_umol_m2_s=_number(control.get("light_umol_m2_s"), "light", (0.0, 5000.0)),
            heater_command=_number(control.get("heater_command"), "heater_command", (0.0, 1.0)),
            heater_applied=_number(control.get("heater_applied"), "heater_applied", (0.0, 1.0)),
            tripped=_flag(control.get("tripped"), "tripped"),
            safe_state=_flag(control.get("safe_state"), "safe_state"),
            energy_kwh=_number(message.get("energy_kwh"), "energy_kwh", (0.0, math.inf)),
            faults=tuple(faults),
        )
    if kind == "od":
        return OdRow(
            device_id=device_id,
            sequence=sequence,
            timestamp=timestamp,
            context=context,
            od750=_number(measurements.get("od750"), "od750", OD750_RANGE),
            saturated=_flag(measurements.get("saturated"), "saturated"),
        )
    raise Rejected("unknown_kind", repr(kind))


def _context(raw: object) -> Context:
    if not isinstance(raw, dict):
        raise Rejected("missing_field", "context")
    hold = raw.get("hold_number")
    if hold is not None and (isinstance(hold, bool) or not isinstance(hold, int)):
        raise Rejected("invalid_field", "hold_number")
    return Context(
        command_id=_text(raw, "command_id"),
        experiment_id=_text(raw, "experiment_id"),
        hold_number=hold,
        hold_elapsed_h=_number(raw.get("hold_elapsed_h"), "hold_elapsed_h", (0.0, math.inf)),
    )


def _text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise Rejected("missing_field", key)
    return value


def _object(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise Rejected("missing_field", key)
    return value


def _number(value: object, name: str, bounds: tuple[float, float]) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Rejected("invalid_field", name)
    if not math.isfinite(value):
        raise Rejected("invalid_field", name)
    if not bounds[0] <= value <= bounds[1]:
        raise Rejected("implausible_value", f"{name}={value}")
    return float(value)


def _flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise Rejected("invalid_field", name)
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise Rejected("missing_field", "timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise Rejected("invalid_timestamp", value) from exc
    if parsed.tzinfo is None:
        raise Rejected("invalid_timestamp", "no timezone")
    return parsed.astimezone(UTC).isoformat()
