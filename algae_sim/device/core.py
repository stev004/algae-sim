"""Device firmware for a bank of simulated photobioreactors. Standard library only.

What a real reactor controller would do lives here, independent of any lab software:

* declare its **controlled variables** (temperature, light intensity, pH), their
  ranges, its protection limits and the resources it meters (energy, CO2);
* accept a setpoint command per reactor that names **exactly** those variables,
  **re-check its own limits**, and start a fresh batch (a simulated re-inoculation)
  on that reactor;
* treat a repeated ``command_id`` as the same command (idempotent): a client that
  retries after a lost reply gets the original acknowledgement, not a second batch;
* run each reactor's own thermostat, pH controller (CO2 dosing) and protection
  trips (over-temperature, pH low, pH high);
* report only attested telemetry: measured temperature and pH, applied setpoints,
  trips, faults, cumulative energy and CO2, and the command it is holding;
* enter a safe state (heater off, lights off, CO2 shut off) on request;
* keep a **device clock**. Time only moves forward. ``advance_to`` steps every
  active reactor up to a given instant;
* optionally **capture** what it measures, as numbered messages in an outbox: an
  environment sample every ``CAPTURE_INTERVAL_S`` of device time and an event for
  every OD reading. The service writes the outbox to an append-only log that a
  separate uploader ships to the data pipeline (docs/data-pipeline.md).

All values are illustrative; the growth model is not validated.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from algae_sim import __version__
from algae_sim.reactor import Reactor, ReactorConfig

SETPOINT_MIN_C = 20.0  # no cooling, so nothing below ambient
SETPOINT_MAX_C = 36.0
LIGHT_MAX = 500.0
PH_MIN = 6.0
PH_MAX = 9.0
MAX_HOLD_H = 96.0
TRIP_C = 39.0
PH_LOW_TRIP = 5.5
PH_HIGH_TRIP = 9.8
OD_MAX = 4.0
CAPTURE_INTERVAL_S = 600.0  # one environment sample per reactor per 10 device-minutes
CAPTURE_SCHEMA_VERSION = "3"
# Bumped when the pickled DeviceCore layout changes; older state files are refused.
STATE_VERSION = 3


@dataclass(frozen=True, slots=True)
class Variable:
    """One controlled variable as the device declares it. Values are in ``unit``."""

    unit: str
    minimum: float
    maximum: float
    resolution: float
    tracked: bool  # True if the device measures it, so the setpoint can be checked

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "resolution": self.resolution,
            "tracked": self.tracked,
        }


# The device's contract, in one place. Names and units follow AutoLab's culture
# contract v2; AutoLab reads them from the manifest rather than assuming them.
VARIABLES: dict[str, Variable] = {
    "temperature": Variable("degree_Celsius", SETPOINT_MIN_C, SETPOINT_MAX_C, 0.1, True),
    "light_intensity": Variable("micromole / meter ** 2 / second", 0.0, LIGHT_MAX, 1.0, False),
    "ph": Variable("dimensionless", PH_MIN, PH_MAX, 0.05, True),
}
PROTECTION_LIMITS: dict[str, dict[str, float]] = {
    "temperature": {"maximum": TRIP_C},
    "ph": {"minimum": PH_LOW_TRIP, "maximum": PH_HIGH_TRIP},
}
RESOURCES: dict[str, str] = {"energy": "kilowatt_hour", "co2": "gram"}


class DeviceError(Exception):
    """A refusal or failure the device reports, with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DeviceSettings:
    """Device configuration. Faults are part of the (simulated) hardware, not the client."""

    seed: int = 0
    reactor_ids: tuple[str, ...] = ("reactor-1", "reactor-2", "reactor-3")
    ambient_c: float = 20.0
    # Fault injection, per reactor: {reactor_id: [hold numbers]}. Hold numbers count
    # the setpoint commands that reactor has accepted, from 0. With one experiment
    # per hold, hold N is the reactor's part of experiment N.
    heater_faults: Mapping[str, tuple[int, ...]] | None = None
    heater_fault_after_h: float = 4.0
    culture_crashes: Mapping[str, tuple[int, ...]] | None = None
    culture_crash_after_h: float = 12.0
    temperature_dropout_probability: float = 0.0
    temperature_sensor_offset_c: float = 0.0
    co2_valve_faults: Mapping[str, tuple[int, ...]] | None = None
    co2_valve_fault_mode: str = "open"  # the valve sticks "open" or "closed"
    co2_valve_fault_after_h: float = 4.0
    ph_dropout_probability: float = 0.0
    ph_sensor_offset: float = 0.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> DeviceSettings:
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise DeviceError("settings_invalid", f"unknown settings: {sorted(unknown)}")
        values: dict[str, Any] = dict(raw)
        if "reactor_ids" in values:
            values["reactor_ids"] = tuple(values["reactor_ids"])  # type: ignore[arg-type]
        for key in FAULT_TABLES:
            if values.get(key) is not None:
                values[key] = {
                    str(r): tuple(int(i) for i in v)
                    for r, v in values[key].items()  # type: ignore[union-attr]
                }
        settings = cls(**values)
        if not settings.reactor_ids:
            raise DeviceError("settings_invalid", "need at least one reactor")
        if settings.co2_valve_fault_mode not in ("open", "closed"):
            raise DeviceError("settings_invalid", "co2_valve_fault_mode must be open or closed")
        for key in FAULT_TABLES:
            stray = set(getattr(settings, key) or {}) - set(settings.reactor_ids)
            if stray:
                raise DeviceError(
                    "settings_invalid", f"{key} names unknown reactors {sorted(stray)}"
                )
        return settings

    def faulted(self, kind: str, reactor_id: str, hold: int) -> bool:
        table: Mapping[str, tuple[int, ...]] = getattr(self, kind) or {}
        return hold in table.get(reactor_id, ())

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "reactor_ids": list(self.reactor_ids),
            "ambient_c": self.ambient_c,
            "heater_faults": {k: list(v) for k, v in (self.heater_faults or {}).items()},
            "heater_fault_after_h": self.heater_fault_after_h,
            "culture_crashes": {k: list(v) for k, v in (self.culture_crashes or {}).items()},
            "culture_crash_after_h": self.culture_crash_after_h,
            "temperature_dropout_probability": self.temperature_dropout_probability,
            "temperature_sensor_offset_c": self.temperature_sensor_offset_c,
            "co2_valve_faults": {k: list(v) for k, v in (self.co2_valve_faults or {}).items()},
            "co2_valve_fault_mode": self.co2_valve_fault_mode,
            "co2_valve_fault_after_h": self.co2_valve_fault_after_h,
            "ph_dropout_probability": self.ph_dropout_probability,
            "ph_sensor_offset": self.ph_sensor_offset,
        }


FAULT_TABLES = ("heater_faults", "culture_crashes", "co2_valve_faults")


@dataclass(slots=True)
class Batch:
    reactor: Reactor
    started_at: datetime
    command_id: str
    experiment_id: str
    applied: dict[str, float]  # the setpoints the device accepted, by variable name
    acknowledgement: dict[str, Any] = field(default_factory=dict)
    next_capture_s: float = 0.0  # hold-elapsed time of the next environment sample
    was_tripped: bool = False  # protection state at the last step, to catch new trips


class DeviceCore:
    def __init__(self, settings: DeviceSettings, *, start_time: datetime) -> None:
        if start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware")
        self.settings = settings
        self.state_version = STATE_VERSION
        self.now = start_time
        # The probe's calibration belongs to the device and survives restarts.
        self.calibrated_at = start_time - timedelta(days=1)
        self.batches: dict[str, Batch] = {}
        self.holds: dict[str, int] = {}
        self.acknowledged: dict[str, dict[str, Any]] = {}  # command_id -> ack (idempotency)
        # Capture is off unless a host (the device service) enables it and drains it.
        self.capture_source: str | None = None
        self.outbox: list[dict[str, Any]] = []
        self.capture_sequences: dict[str, int] = {}  # per reactor; never reused

    def enable_capture(self, source: str) -> None:
        """Start recording messages. ``source`` prefixes each reactor's device ID."""
        self.capture_source = source

    def drain_outbox(self) -> list[dict[str, Any]]:
        messages, self.outbox = self.outbox, []
        return messages

    # -- clock ---------------------------------------------------------------------

    def advance_to(self, when: datetime) -> datetime:
        """Move device time forward to ``when`` (never backwards), stepping every reactor."""
        if when > self.now:
            self.now = when
        for reactor_id, batch in self.batches.items():
            target_s = (self.now - batch.started_at).total_seconds()
            reactor = batch.reactor
            dt = reactor.config.dt_s
            while reactor.elapsed_s + dt <= target_s + 1e-9:
                reactor.step()
                tripped = _tripped(reactor)
                if self.capture_source:
                    if reactor.elapsed_s + 1e-9 >= batch.next_capture_s:
                        self._capture_environment(reactor_id, batch)
                    elif tripped and not batch.was_tripped:
                        # Log a trip the moment it happens, like a real alarm log. A
                        # client may safe the reactor within minutes, and samples
                        # taken in safe state don't describe the hold.
                        self._capture_environment(reactor_id, batch, scheduled=False)
                batch.was_tripped = tripped
        return self.now

    # -- description ---------------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        return {
            "device_type": "photobioreactor_environment",
            "firmware_version": f"algae-sim {__version__}",
            "reactor_ids": list(self.settings.reactor_ids),
            "controlled_variables": {name: v.as_dict() for name, v in VARIABLES.items()},
            "protection_limits": {name: dict(limit) for name, limit in PROTECTION_LIMITS.items()},
            "resources": dict(RESOURCES),
            "maximum_hold_h": MAX_HOLD_H,
            "od750_range": [0.0, OD_MAX],
            "od750_calibrated_at": self.calibrated_at.isoformat(),
            "is_simulated": True,
            "model": "illustrative, not validated",
            "seed": self.settings.seed,
        }

    # -- commands ----------------------------------------------------------------

    def apply_setpoints(
        self,
        *,
        command_id: str,
        experiment_id: str,
        reactor_id: str,
        setpoints: Mapping[str, float],
    ) -> dict[str, Any]:
        """Start a hold. ``setpoints`` must name exactly ``VARIABLES``, in their units."""
        previous = self.acknowledged.get(command_id)
        if previous is not None:
            return {**previous, "duplicate": True}
        refusal = self._refusal(reactor_id, setpoints)
        if refusal is not None:
            ack = {
                "command_id": command_id,
                "reactor_id": reactor_id,
                "accepted": False,
                "rejection_code": refusal[0],
                "rejection_message": refusal[1],
                "acknowledged_at": self.now.isoformat(),
            }
            self.acknowledged[command_id] = ack
            return ack
        self.advance_to(self.now)  # bring other reactors up to date first
        hold = self.holds.get(reactor_id, 0)
        self.holds[reactor_id] = hold + 1
        s = self.settings
        applied = {name: float(setpoints[name]) for name in VARIABLES}
        config = ReactorConfig(
            temperature_setpoint_c=applied["temperature"],
            light_umol_m2_s=applied["light_intensity"],
            ph_setpoint=applied["ph"],
            batch_hours=MAX_HOLD_H,
            ambient_c=s.ambient_c,
            start_temperature_c=s.ambient_c,
            seed=self._seed(reactor_id, hold),
            heater_fails_after_h=(
                s.heater_fault_after_h if s.faulted("heater_faults", reactor_id, hold) else None
            ),
            culture_crash_after_h=(
                s.culture_crash_after_h if s.faulted("culture_crashes", reactor_id, hold) else None
            ),
            temperature_dropout_probability=s.temperature_dropout_probability,
            temperature_sensor_offset_c=s.temperature_sensor_offset_c,
            co2_valve_sticks=(
                s.co2_valve_fault_mode if s.faulted("co2_valve_faults", reactor_id, hold) else None
            ),
            co2_valve_sticks_after_h=s.co2_valve_fault_after_h,
            ph_dropout_probability=s.ph_dropout_probability,
            ph_sensor_offset=s.ph_sensor_offset,
        )
        ack = {
            "command_id": command_id,
            "reactor_id": reactor_id,
            "accepted": True,
            "applied": dict(applied),
            "acknowledged_at": self.now.isoformat(),
            "hold_number": hold,
        }
        self.batches[reactor_id] = Batch(
            reactor=Reactor(config),
            started_at=self.now,
            command_id=command_id,
            experiment_id=experiment_id,
            applied=applied,
            acknowledgement=ack,
        )
        self.acknowledged[command_id] = ack
        if self.capture_source:
            self._capture_environment(reactor_id, self.batches[reactor_id])
        return ack

    def _refusal(
        self, reactor_id: str, setpoints: Mapping[str, float]
    ) -> tuple[str, str] | None:
        if reactor_id not in self.settings.reactor_ids:
            return "unknown_reactor", f"no reactor {reactor_id!r}"
        unknown = sorted(set(setpoints) - set(VARIABLES))
        if unknown:
            return "unknown_variable", f"device has no controlled variable {unknown}"
        missing = sorted(set(VARIABLES) - set(setpoints))
        if missing:
            # Nothing sensible to hold a missing variable at, so refuse the whole command.
            return "missing_variable", f"command must also set {missing}"
        for name, variable in VARIABLES.items():
            value = setpoints[name]
            if not math.isfinite(value) or not variable.minimum <= value <= variable.maximum:
                return (
                    "setpoint_out_of_device_range",
                    f"{name} {value} outside device range "
                    f"[{variable.minimum}, {variable.maximum}] {variable.unit}",
                )
        return None

    def _seed(self, reactor_id: str, hold: int) -> int:
        # "Reactor-2, culture 5", the way a lab labels runs. Deliberately not the
        # client's experiment ID: AutoLab mints fresh IDs for every campaign, so
        # seeding on them made two identical campaigns grow different cultures.
        # Each replicate is still its own culture (the reactor ID differs), and
        # the hold counter is persisted, so a restarted device carries on the
        # same sequence.
        key = f"{self.settings.seed}:{reactor_id}:{hold}"
        return int.from_bytes(hashlib.sha256(key.encode()).digest()[:4], "big")

    def safe_state(self, reactor_id: str, reason: str) -> dict[str, Any]:
        self._known(reactor_id)
        self.advance_to(self.now)
        batch = self.batches.get(reactor_id)
        if batch is not None:
            batch.reactor.enter_safe_state()
        return {
            "reactor_id": reactor_id,
            "reason": reason,
            "reached": True,
            "detail": "heater off, lights off, CO2 shut off",
            "acknowledged_at": self.now.isoformat(),
        }

    # -- observation --------------------------------------------------------------

    def telemetry(self, reactor_id: str) -> dict[str, Any]:
        batch = self._active(reactor_id)
        reactor = batch.reactor
        return {
            "reactor_id": reactor_id,
            "sampled_at": self.now.isoformat(),
            "applied": _applied(batch),
            "measured": _measured(reactor),
            "consumption": _consumption(reactor),
            "protection_tripped": _tripped(reactor),
            "trip_count": reactor.controller.trip_count + reactor.ph_controller.trip_count,
            "faults": _faults(reactor),
            "active_command_id": None if reactor.safe_state else batch.command_id,
            "hold_elapsed_h": round(reactor.elapsed_s / 3600, 4),
        }

    def read_od(self, reactor_id: str) -> dict[str, Any] | None:
        self._known(reactor_id)
        batch = self.batches.get(reactor_id)
        if batch is None:
            return None
        self.advance_to(self.now)
        od = batch.reactor.read_od750()
        if self.capture_source:
            self._capture(
                reactor_id,
                batch,
                "od",
                measurements={"od750": od, "saturated": od >= OD_MAX},
                truth={"biomass_g_per_l": round(batch.reactor.true_biomass_g_per_l, 6)},
            )
        return {
            "reactor_id": reactor_id,
            "od750": od,
            "saturated": od >= OD_MAX,
            "measured_at": self.now.isoformat(),
            "hold_elapsed_h": round(batch.reactor.elapsed_s / 3600, 4),
        }

    # -- capture ------------------------------------------------------------------

    def _capture_environment(
        self, reactor_id: str, batch: Batch, *, scheduled: bool = True
    ) -> None:
        r = batch.reactor
        measured = _measured(r)
        applied = _applied(batch)
        consumption = _consumption(r)
        self._capture(
            reactor_id,
            batch,
            "environment",
            measurements={"temperature_c": measured["temperature"], "ph": measured["ph"]},
            control={
                "temperature_setpoint_c": applied["temperature"],
                "light_umol_m2_s": applied["light_intensity"],
                "ph_setpoint": applied["ph"],
                "heater_command": r.last_command,
                "heater_applied": r.last_applied,
                "co2_valve_command": r.last_valve_command,
                "co2_valve_open": r.last_valve_open,
                "tripped": _tripped(r),
                "safe_state": r.safe_state,
            },
            energy_kwh=consumption["energy"],
            co2_g=consumption["co2"],
            faults=_faults(r),
            truth={
                "temperature_c": round(r.true_temperature_c, 4),
                "biomass_g_per_l": round(r.true_biomass_g_per_l, 6),
                "ph": round(r.true_ph, 4),
            },
        )
        if scheduled:  # an event sample leaves the 10-minute schedule alone
            batch.next_capture_s = r.elapsed_s + CAPTURE_INTERVAL_S

    def _capture(
        self,
        reactor_id: str,
        batch: Batch,
        kind: str,
        *,
        measurements: dict[str, Any],
        truth: dict[str, Any],
        **extra: Any,
    ) -> None:
        """Append one message. Its time is the reactor's own step time, not ``now``."""
        sequence = self.capture_sequences.get(reactor_id, 0)
        self.capture_sequences[reactor_id] = sequence + 1
        r = batch.reactor
        self.outbox.append(
            {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "kind": kind,
                "device_id": f"{self.capture_source}/{reactor_id}",
                "sequence": sequence,
                "timestamp": (batch.started_at + timedelta(seconds=r.elapsed_s)).isoformat(),
                "context": {
                    "command_id": batch.command_id,
                    "experiment_id": batch.experiment_id,
                    "hold_number": batch.acknowledgement.get("hold_number"),
                    "hold_elapsed_h": round(r.elapsed_s / 3600, 4),
                },
                "measurements": measurements,
                **extra,
                # Ground truth for testing only; a real device can't know it and the
                # pipeline drops it at the parse step.
                "simulator_truth": truth,
            }
        )

    def status(self) -> dict[str, Any]:
        reactors = {}
        for reactor_id in self.settings.reactor_ids:
            batch = self.batches.get(reactor_id)
            if batch is None:
                reactors[reactor_id] = {"state": "idle", "holds": self.holds.get(reactor_id, 0)}
                continue
            r = batch.reactor
            reactors[reactor_id] = {
                "state": "safe" if r.safe_state else "holding",
                "holds": self.holds.get(reactor_id, 0),
                "command_id": batch.command_id,
                "applied": _applied(batch),
                "measured": _measured(r),
                "hold_elapsed_h": round(r.elapsed_s / 3600, 3),
            }
        return {"device_time": self.now.isoformat(), "reactors": reactors}

    def _known(self, reactor_id: str) -> None:
        if reactor_id not in self.settings.reactor_ids:
            raise DeviceError("unknown_reactor", f"no reactor {reactor_id!r}")

    def _active(self, reactor_id: str) -> Batch:
        self._known(reactor_id)
        batch = self.batches.get(reactor_id)
        if batch is None:
            raise DeviceError("no_active_batch", f"reactor {reactor_id!r} has no batch")
        self.advance_to(self.now)
        return batch


def _applied(batch: Batch) -> dict[str, float]:
    """What the device is applying now. Safe state turns the lights off."""
    applied = dict(batch.applied)
    if batch.reactor.safe_state:
        applied["light_intensity"] = 0.0
    return applied


def _measured(reactor: Reactor) -> dict[str, float | None]:
    """One entry per tracked variable; None is a probe dropout (or no reading yet)."""
    t, ph = reactor.last_measured_c, reactor.last_measured_ph
    return {
        "temperature": None if t is None else round(t, 1),
        "ph": None if ph is None else round(ph, 2),
    }


def _consumption(reactor: Reactor) -> dict[str, float]:
    """Cumulative since the command was applied (each command starts a fresh batch)."""
    return {
        "energy": round(reactor.total_energy_wh / 1000, 6),
        "co2": round(reactor.co2_dosed_g, 4),
    }


def _tripped(reactor: Reactor) -> bool:
    return reactor.controller.tripped or reactor.ph_controller.tripped


def _faults(reactor: Reactor) -> list[str]:
    """Fault codes. Trip codes end in ``_trip`` and name the limit that tripped."""
    faults = []
    if reactor.heater.failed:
        faults.append("heater_not_responding")
    if reactor.co2_valve.stuck is not None:
        faults.append("co2_valve_not_responding")
    if reactor.elapsed_s > 0:
        if reactor.last_measured_c is None:
            faults.append("temperature_probe_dropout")
        if reactor.last_measured_ph is None:
            faults.append("ph_probe_dropout")
    if reactor.controller.tripped:
        faults.append("over_temperature_trip")
    if reactor.ph_controller.low_tripped:
        faults.append("ph_low_trip")
    if reactor.ph_controller.high_tripped:
        faults.append("ph_high_trip")
    return faults
