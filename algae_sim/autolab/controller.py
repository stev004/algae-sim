"""In-process AutoLab culture controller (adapter ``algae-sim-reactor``).

Hosts the device firmware (``algae_sim.device.core.DeviceCore``) inside the AutoLab
process. It is fast and simple, but the simulated reactors die with AutoLab, so after
a restart AutoLab finds no hold to confirm and ends it safely. For a device that
outlives AutoLab, run ``python -m algae_sim device serve`` and use the network
adapter ``algae-sim-device`` (``remote.py``) instead.

AutoLab sends one bounded ``SetpointCommand`` per replicate reactor, then holds: it
sleeps on its clock, polls telemetry and reads OD750. Before answering anything, this
adapter advances the device clock to AutoLab's ``clock.now()``, so with AutoLab's
``SteppedClock`` a 36 h hold takes milliseconds. The firmware re-checks its own
limits, acknowledges applied values, reports only attested telemetry (including the
command it holds), and owns its thermostat, its pH controller (CO2 dosing) and its
protection trips.

The adapter does not choose experiments or read objectives or optimiser state.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from autonomous_lab.culture.controller import (
    ControllerError,
    EnvironmentTelemetry,
    SafeStateAcknowledgement,
    SetpointAcknowledgement,
    SetpointCommand,
)
from autonomous_lab.culture.models import CultureControllerCapabilities

from algae_sim.autolab import _convert as convert
from algae_sim.autolab.sensor import OdSample, ReactorODSensor
from algae_sim.device.core import DeviceCore, DeviceError, DeviceSettings

ADAPTER_NAME = "algae-sim-reactor"
TEMP = convert.TEMP
LIGHT = convert.LIGHT


class Clock(Protocol):
    def now(self) -> datetime: ...


class _UtcNow:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SimulatedReactorController:
    def __init__(
        self, *, controller_id: str, settings: DeviceSettings, clock: Clock | None = None
    ) -> None:
        if not controller_id:
            raise ValueError("controller_id must not be empty")
        self._controller_id = controller_id
        self._clock: Clock = clock or _UtcNow()
        self.core = DeviceCore(settings, start_time=self._clock.now())
        sensor_id = f"{controller_id}-od750"
        self._sensor = ReactorODSensor(
            sensor_id=sensor_id,
            source=self,
            calibration=convert.calibration(sensor_id, self.core.manifest()),
            clock=self._sync,
        )

    @property
    def controller_id(self) -> str:
        return self._controller_id

    def _sync(self) -> datetime:
        return self.core.advance_to(self._clock.now())

    async def capability_manifest(self) -> CultureControllerCapabilities:
        return convert.capabilities(self._controller_id, ADAPTER_NAME, self.core.manifest())

    async def apply_setpoints(self, command: SetpointCommand) -> SetpointAcknowledgement:
        self._sync()
        try:
            fields = convert.command_fields(command, self.core.manifest())
        except ValueError as exc:
            return SetpointAcknowledgement(
                command_id=command.command_id,
                controller_id=self._controller_id,
                reactor_id=command.reactor_id,
                accepted=False,
                rejection_code="incompatible_units",
                rejection_message=str(exc),
                acknowledged_at=self.core.now,
            )
        ack = self.core.apply_setpoints(**fields)
        return convert.acknowledgement(self._controller_id, ack, self.core.manifest())

    async def read_telemetry(self, reactor_id: str) -> EnvironmentTelemetry:
        self._sync()
        sample = self._call(self.core.telemetry, reactor_id)
        return convert.telemetry(self._controller_id, sample, self.core.manifest())

    async def enter_safe_state(self, reactor_id: str, reason: str) -> SafeStateAcknowledgement:
        self._sync()
        ack = self._call(self.core.safe_state, reactor_id, reason)
        return convert.safe_state(self._controller_id, ack)

    def culture_sensor(self) -> ReactorODSensor:
        """The integrated OD750 probe (AutoLab's ``IntegratedCultureSensor``)."""
        return self._sensor

    def sample_od(self, reactor_id: str) -> OdSample | None:
        self._sync()
        try:
            return convert.od_sample(self.core.read_od(reactor_id))
        except DeviceError:
            return None

    @staticmethod
    def _call(fn, *args):  # type: ignore[no-untyped-def]
        try:
            return fn(*args)
        except DeviceError as exc:
            raise ControllerError(exc.code, str(exc)) from exc


def create_controller(
    *, controller_id: str, settings: Mapping[str, object], clock: object | None = None
) -> SimulatedReactorController:
    """Entry-point factory for AutoLab's ``autonomous_lab.culture_controllers`` group."""
    if clock is not None and not callable(getattr(clock, "now", None)):
        raise ControllerError("controller_clock_invalid", "clock must provide now()")
    try:
        device_settings = DeviceSettings.from_mapping(settings)
    except DeviceError as exc:
        raise ControllerError("controller_settings_invalid", str(exc)) from exc
    return SimulatedReactorController(
        controller_id=controller_id,
        settings=device_settings,
        clock=clock,  # type: ignore[arg-type]
    )
