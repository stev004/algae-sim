"""Device firmware for a bank of simulated photobioreactors. Standard library only.

What a real reactor controller would do lives here, independent of any lab software:

* accept a setpoint command per reactor, **re-check its own limits**, and start a
  fresh batch (a simulated re-inoculation) on that reactor;
* treat a repeated ``command_id`` as the same command (idempotent): a client that
  retries after a lost reply gets the original acknowledgement, not a second batch;
* run each reactor's own thermostat, fail-safe and over-temperature trip;
* report only attested telemetry: measured temperature, applied setpoints, trips,
  faults, energy, and the command it is holding;
* enter a safe state (heater and lights off) on request;
* keep a **device clock**. Time only moves forward. ``advance_to`` steps every
  active reactor up to a given instant.

All values are illustrative; the growth model is not validated.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from algae_sim import __version__
from algae_sim.reactor import Reactor, ReactorConfig

SETPOINT_MIN_C = 20.0  # no cooling, so nothing below ambient
SETPOINT_MAX_C = 36.0
LIGHT_MAX = 500.0
MAX_HOLD_H = 96.0
TRIP_C = 39.0
OD_MAX = 4.0


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

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> DeviceSettings:
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise DeviceError("settings_invalid", f"unknown settings: {sorted(unknown)}")
        values: dict[str, Any] = dict(raw)
        if "reactor_ids" in values:
            values["reactor_ids"] = tuple(values["reactor_ids"])  # type: ignore[arg-type]
        for key in ("heater_faults", "culture_crashes"):
            if values.get(key) is not None:
                values[key] = {
                    str(r): tuple(int(i) for i in v)
                    for r, v in values[key].items()  # type: ignore[union-attr]
                }
        settings = cls(**values)
        if not settings.reactor_ids:
            raise DeviceError("settings_invalid", "need at least one reactor")
        for key in ("heater_faults", "culture_crashes"):
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
        }


@dataclass(slots=True)
class Batch:
    reactor: Reactor
    started_at: datetime
    command_id: str
    experiment_id: str
    applied_temperature_c: float
    applied_light: float
    acknowledgement: dict[str, Any] = field(default_factory=dict)


class DeviceCore:
    def __init__(self, settings: DeviceSettings, *, start_time: datetime) -> None:
        if start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware")
        self.settings = settings
        self.now = start_time
        # The probe's calibration belongs to the device and survives restarts.
        self.calibrated_at = start_time - timedelta(days=1)
        self.batches: dict[str, Batch] = {}
        self.holds: dict[str, int] = {}
        self.acknowledged: dict[str, dict[str, Any]] = {}  # command_id -> ack (idempotency)

    # -- clock ---------------------------------------------------------------------

    def advance_to(self, when: datetime) -> datetime:
        """Move device time forward to ``when`` (never backwards), stepping every reactor."""
        if when > self.now:
            self.now = when
        for batch in self.batches.values():
            target_s = (self.now - batch.started_at).total_seconds()
            reactor = batch.reactor
            dt = reactor.config.dt_s
            while reactor.elapsed_s + dt <= target_s + 1e-9:
                reactor.step()
        return self.now

    # -- description ---------------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        return {
            "device_type": "photobioreactor_environment",
            "firmware_version": f"algae-sim {__version__}",
            "reactor_ids": list(self.settings.reactor_ids),
            "temperature_setpoint_c": [SETPOINT_MIN_C, SETPOINT_MAX_C],
            "light_umol_m2_s": [0.0, LIGHT_MAX],
            "maximum_hold_h": MAX_HOLD_H,
            "over_temperature_trip_c": TRIP_C,
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
        temperature_c: float,
        light_umol_m2_s: float,
    ) -> dict[str, Any]:
        previous = self.acknowledged.get(command_id)
        if previous is not None:
            return {**previous, "duplicate": True}
        refusal = self._refusal(reactor_id, temperature_c, light_umol_m2_s)
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
        config = ReactorConfig(
            temperature_setpoint_c=temperature_c,
            light_umol_m2_s=light_umol_m2_s,
            batch_hours=MAX_HOLD_H,
            ambient_c=s.ambient_c,
            start_temperature_c=s.ambient_c,
            seed=self._seed(experiment_id, reactor_id),
            heater_fails_after_h=(
                s.heater_fault_after_h if s.faulted("heater_faults", reactor_id, hold) else None
            ),
            culture_crash_after_h=(
                s.culture_crash_after_h if s.faulted("culture_crashes", reactor_id, hold) else None
            ),
            temperature_dropout_probability=s.temperature_dropout_probability,
            temperature_sensor_offset_c=s.temperature_sensor_offset_c,
        )
        ack = {
            "command_id": command_id,
            "reactor_id": reactor_id,
            "accepted": True,
            "applied_temperature_c": temperature_c,
            "applied_light_umol_m2_s": light_umol_m2_s,
            "acknowledged_at": self.now.isoformat(),
            "hold_number": hold,
        }
        self.batches[reactor_id] = Batch(
            reactor=Reactor(config),
            started_at=self.now,
            command_id=command_id,
            experiment_id=experiment_id,
            applied_temperature_c=temperature_c,
            applied_light=light_umol_m2_s,
            acknowledgement=ack,
        )
        self.acknowledged[command_id] = ack
        return ack

    def _refusal(
        self, reactor_id: str, temperature_c: float, light: float
    ) -> tuple[str, str] | None:
        if reactor_id not in self.settings.reactor_ids:
            return "unknown_reactor", f"no reactor {reactor_id!r}"
        if not SETPOINT_MIN_C <= temperature_c <= SETPOINT_MAX_C:
            return "setpoint_out_of_device_range", f"{temperature_c} °C outside device range"
        if not 0.0 <= light <= LIGHT_MAX:
            return "light_out_of_device_range", f"{light} outside device range"
        return None

    def _seed(self, experiment_id: str, reactor_id: str) -> int:
        # Tied to experiment and reactor, not call order: reproducible, and each
        # replicate is a different (but repeatable) culture.
        key = f"{self.settings.seed}:{experiment_id}:{reactor_id}"
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
            "detail": "heater off, lights off",
            "acknowledged_at": self.now.isoformat(),
        }

    # -- observation --------------------------------------------------------------

    def telemetry(self, reactor_id: str) -> dict[str, Any]:
        batch = self._active(reactor_id)
        reactor = batch.reactor
        measured = reactor.last_measured_c
        faults = []
        if reactor.heater.failed:
            faults.append("heater_not_responding")
        if measured is None and reactor.elapsed_s > 0:
            faults.append("temperature_probe_dropout")
        return {
            "reactor_id": reactor_id,
            "sampled_at": self.now.isoformat(),
            "measured_temperature_c": None if measured is None else round(measured, 1),
            "applied_temperature_c": batch.applied_temperature_c,
            "applied_light_umol_m2_s": 0.0 if reactor.safe_state else batch.applied_light,
            "protection_tripped": reactor.controller.tripped,
            "trip_count": reactor.controller.trip_count,
            "cumulative_energy_kwh": round(reactor.total_energy_wh / 1000, 6),
            "faults": faults,
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
        return {
            "reactor_id": reactor_id,
            "od750": od,
            "saturated": od >= OD_MAX,
            "measured_at": self.now.isoformat(),
            "hold_elapsed_h": round(batch.reactor.elapsed_s / 3600, 4),
        }

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
                "setpoint_c": batch.applied_temperature_c,
                "light_umol_m2_s": batch.applied_light,
                "hold_elapsed_h": round(r.elapsed_s / 3600, 3),
                "measured_temperature_c": r.last_measured_c,
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
