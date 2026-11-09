"""The simulated photobioreactor as an AutoLab stage-6 culture controller.

AutoLab sends one bounded ``SetpointCommand`` (temperature setpoint + light) per
experiment, then holds: it sleeps on its clock, polls telemetry and reads OD750.
This adapter is the device side of that conversation:

* **Re-checks its own limits.** A command outside this device's manifest, or too
  close to its over-temperature trip, is refused even if AutoLab validated it.
* **Acknowledges what it applied.** Each accepted command starts a fresh batch
  (a simulated re-inoculation) and reports the setpoints it actually applied.
* **Reports only attested telemetry.** Measured temperature (``None`` on a probe
  dropout), applied setpoints, trips, faults and cumulative energy. Never the
  hidden true state.
* **Owns protection.** The reactor's own controller keeps its fail-safe and trip,
  whatever AutoLab does. ``enter_safe_state`` turns the heater and lights off.
* **Follows AutoLab's clock.** Before answering anything, the simulated world is
  advanced to ``clock.now()``. With AutoLab's ``SteppedClock`` a 36 h hold takes
  well under a second; nothing here waits in real time.

The adapter does not choose experiments or read objectives or optimiser state.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from autonomous_lab.culture.controller import (
    ControllerError,
    EnvironmentTelemetry,
    SafeStateAcknowledgement,
    SetpointAcknowledgement,
    SetpointCommand,
)
from autonomous_lab.culture.models import CultureControllerCapabilities
from autonomous_lab.domain.protocols import QuantityRange
from autonomous_lab.domain.quantities import QuantityValue

from algae_sim import __version__
from algae_sim.autolab.sensor import ReactorODSensor, default_calibration
from algae_sim.reactor import Reactor, ReactorConfig

ADAPTER_NAME = "algae-sim-reactor"
TEMP = "degree_Celsius"
LIGHT = "micromole / meter ** 2 / second"
# Device limits (illustrative). No cooling, so setpoints start at ambient.
SETPOINT_MIN_C = Decimal(20)
SETPOINT_MAX_C = Decimal(36)
LIGHT_MAX = Decimal(500)
MAX_HOLD_H = Decimal(96)
TRIP_C = Decimal(39)


class Clock(Protocol):
    def now(self) -> datetime: ...


class _UtcNow:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _q(value: float | Decimal | int, unit: str) -> QuantityValue:
    return QuantityValue(value=Decimal(str(value)), unit=unit)


@dataclass(frozen=True, slots=True)
class ControllerSettings:
    """Adapter-owned settings from the campaign's ``execution.controller.settings``."""

    seed: int = 0
    reactor_ids: tuple[str, ...] = ("reactor-1",)
    ambient_c: float = 20.0
    # Fault injection: batches (0-based, in the order this controller runs them)
    # whose heater fails ``heater_fault_after_h`` hours into the hold.
    heater_fault_batches: tuple[int, ...] = ()
    heater_fault_after_h: float = 4.0
    temperature_dropout_probability: float = 0.0
    temperature_sensor_offset_c: float = 0.0

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ControllerSettings:
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise ControllerError(
                "controller_settings_invalid", f"unknown settings: {sorted(unknown)}"
            )
        values: dict[str, Any] = dict(raw)
        for key in ("reactor_ids", "heater_fault_batches"):
            if key in values:
                values[key] = tuple(values[key])  # type: ignore[arg-type]
        settings = cls(**values)
        if not settings.reactor_ids:
            raise ControllerError("controller_settings_invalid", "need at least one reactor")
        return settings


@dataclass(slots=True)
class _Batch:
    reactor: Reactor
    started_at: datetime
    command: SetpointCommand
    applied_temperature: QuantityValue
    applied_light: QuantityValue


class SimulatedReactorController:
    def __init__(
        self, *, controller_id: str, settings: ControllerSettings, clock: Clock | None = None
    ) -> None:
        if not controller_id:
            raise ValueError("controller_id must not be empty")
        self._controller_id = controller_id
        self._settings = settings
        self._clock: Clock = clock or _UtcNow()
        self._batches: dict[str, _Batch] = {}
        self._batch_count = 0
        sensor_id = f"{controller_id}-od750"
        self._sensor = ReactorODSensor(
            sensor_id=sensor_id,
            bench=self,
            calibration=default_calibration(
                sensor_id, calibrated_at=self._clock.now() - timedelta(days=1)
            ),
            clock=self._clock.now,
        )

    # -- identity and manifest -------------------------------------------------

    @property
    def controller_id(self) -> str:
        return self._controller_id

    async def capability_manifest(self) -> CultureControllerCapabilities:
        return CultureControllerCapabilities(
            controller_id=self._controller_id,
            device_type="photobioreactor_environment",
            adapter_name=ADAPTER_NAME,
            adapter_version=__version__,
            firmware_version="sim",
            reactor_ids=frozenset(self._settings.reactor_ids),
            temperature_setpoint_range=QuantityRange(
                minimum=_q(SETPOINT_MIN_C, TEMP), maximum=_q(SETPOINT_MAX_C, TEMP)
            ),
            light_intensity_range=QuantityRange(minimum=_q(0, LIGHT), maximum=_q(LIGHT_MAX, LIGHT)),
            maximum_hold_duration=_q(MAX_HOLD_H, "hour"),
            over_temperature_trip=_q(TRIP_C, TEMP),
            is_simulated=True,
            metadata={"model": "illustrative, not validated", "seed": self._settings.seed},
        )

    # -- commands ----------------------------------------------------------------

    async def apply_setpoints(self, command: SetpointCommand) -> SetpointAcknowledgement:
        reason = self._refusal(command)
        if reason is not None:
            code, message = reason
            return SetpointAcknowledgement(
                command_id=command.command_id,
                controller_id=self._controller_id,
                reactor_id=command.reactor_id,
                accepted=False,
                rejection_code=code,
                rejection_message=message,
                acknowledged_at=self._clock.now(),
            )
        setpoint_c = float(command.temperature_setpoint.to(TEMP).value)
        light = float(command.light_intensity.to(LIGHT).value)
        index = self._batch_count
        self._batch_count += 1
        faulty = index in self._settings.heater_fault_batches
        config = ReactorConfig(
            temperature_setpoint_c=setpoint_c,
            light_umol_m2_s=light,
            batch_hours=float(MAX_HOLD_H),
            ambient_c=self._settings.ambient_c,
            start_temperature_c=self._settings.ambient_c,
            seed=self._batch_seed(command),
            heater_fails_after_h=self._settings.heater_fault_after_h if faulty else None,
            temperature_dropout_probability=self._settings.temperature_dropout_probability,
            temperature_sensor_offset_c=self._settings.temperature_sensor_offset_c,
        )
        applied_t = _q(setpoint_c, TEMP)
        applied_l = _q(light, LIGHT)
        self._batches[command.reactor_id] = _Batch(
            reactor=Reactor(config),
            started_at=self._clock.now(),
            command=command,
            applied_temperature=applied_t,
            applied_light=applied_l,
        )
        return SetpointAcknowledgement(
            command_id=command.command_id,
            controller_id=self._controller_id,
            reactor_id=command.reactor_id,
            accepted=True,
            applied_temperature_setpoint=applied_t,
            applied_light_intensity=applied_l,
            acknowledged_at=self._clock.now(),
        )

    def _refusal(self, command: SetpointCommand) -> tuple[str, str] | None:
        if command.reactor_id not in self._settings.reactor_ids:
            return "unknown_reactor", f"no reactor {command.reactor_id!r}"
        try:
            setpoint = command.temperature_setpoint.to(TEMP).value
            light = command.light_intensity.to(LIGHT).value
        except ValueError as exc:
            return "incompatible_units", str(exc)
        if not SETPOINT_MIN_C <= setpoint <= SETPOINT_MAX_C:
            return "setpoint_out_of_device_range", f"{setpoint} °C outside device range"
        if not 0 <= light <= LIGHT_MAX:
            return "light_out_of_device_range", f"{light} outside device range"
        return None

    def _batch_seed(self, command: SetpointCommand) -> int:
        # Tied to the experiment, not to call order, so a resumed campaign repeats it.
        digest = hashlib.sha256(f"{self._settings.seed}:{command.experiment_id}".encode())
        return int.from_bytes(digest.digest()[:4], "big")

    async def enter_safe_state(self, reactor_id: str, reason: str) -> SafeStateAcknowledgement:
        if reactor_id not in self._settings.reactor_ids:
            raise ControllerError("unknown_reactor", f"no reactor {reactor_id!r}")
        batch = self._batches.get(reactor_id)
        if batch is not None:
            self._advance(batch)
            batch.reactor.enter_safe_state()
        return SafeStateAcknowledgement(
            controller_id=self._controller_id,
            reactor_id=reactor_id,
            reason=reason,
            reached=True,
            acknowledged_at=self._clock.now(),
            detail="heater off, lights off",
        )

    # -- observation ------------------------------------------------------------

    async def read_telemetry(self, reactor_id: str) -> EnvironmentTelemetry:
        batch = self._active(reactor_id)
        reactor = batch.reactor
        measured = reactor.last_measured_c
        faults: list[str] = []
        if reactor.heater.failed:
            faults.append("heater_not_responding")
        if measured is None and reactor.elapsed_s > 0:
            faults.append("temperature_probe_dropout")
        return EnvironmentTelemetry(
            controller_id=self._controller_id,
            reactor_id=reactor_id,
            sampled_at=self._clock.now(),
            measured_temperature=None if measured is None else _q(round(measured, 1), TEMP),
            applied_temperature_setpoint=batch.applied_temperature,
            applied_light_intensity=_q(0, LIGHT) if reactor.safe_state else batch.applied_light,
            protection_tripped=reactor.controller.tripped,
            trip_count=reactor.controller.trip_count,
            cumulative_energy=_q(round(reactor.total_energy_wh / 1000, 6), "kilowatt_hour"),
            faults=tuple(faults),
        )

    def culture_sensor(self) -> ReactorODSensor:
        """The integrated OD750 probe (AutoLab's ``IntegratedCultureSensor``)."""
        return self._sensor

    def get(self, reactor_id: str) -> Reactor | None:
        """Reactor lookup for the integrated probe; advances the world first."""
        batch = self._batches.get(reactor_id)
        if batch is None:
            return None
        self._advance(batch)
        return batch.reactor

    def _active(self, reactor_id: str) -> _Batch:
        if reactor_id not in self._settings.reactor_ids:
            raise ControllerError("unknown_reactor", f"no reactor {reactor_id!r}")
        batch = self._batches.get(reactor_id)
        if batch is None:
            raise ControllerError("no_active_batch", f"reactor {reactor_id!r} has no batch")
        self._advance(batch)
        return batch

    def _advance(self, batch: _Batch) -> None:
        """Step the simulation forward to the clock's current time."""
        target_s = (self._clock.now() - batch.started_at).total_seconds()
        reactor = batch.reactor
        dt = reactor.config.dt_s
        while reactor.elapsed_s + dt <= target_s + 1e-9:
            reactor.step()


def create_controller(
    *, controller_id: str, settings: Mapping[str, object], clock: object | None = None
) -> SimulatedReactorController:
    """Entry-point factory for AutoLab's ``autonomous_lab.culture_controllers`` group."""
    if clock is not None and not callable(getattr(clock, "now", None)):
        raise ControllerError("controller_clock_invalid", "clock must provide now()")
    return SimulatedReactorController(
        controller_id=controller_id,
        settings=ControllerSettings.from_mapping(settings),
        clock=clock,  # type: ignore[arg-type]
    )
