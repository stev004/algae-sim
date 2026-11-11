"""Translate between the device firmware's plain JSON and AutoLab's typed models.

Shared by the in-process adapter (``controller.py``) and the network adapter
(``remote.py``), so both present the device to AutoLab identically.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_lab.culture.controller import (
    EnvironmentTelemetry,
    SafeStateAcknowledgement,
    SetpointAcknowledgement,
    SetpointCommand,
)
from autonomous_lab.culture.models import (
    ControlledVariable,
    CultureControllerCapabilities,
    ProtectionLimit,
    interval_unit,
)
from autonomous_lab.domain.protocols import QuantityRange
from autonomous_lab.domain.quantities import QuantityValue
from autonomous_lab.hardware.sensors.calibration import CalibrationRecord

from algae_sim import __version__
from algae_sim.autolab.sensor import MEASUREMENT_TYPE, OdSample

TEMP = "degree_Celsius"
LIGHT = "micromole / meter ** 2 / second"


def q(value: float | Decimal | int, unit: str) -> QuantityValue:
    return QuantityValue(value=Decimal(str(value)), unit=unit)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def variable_units(manifest: dict[str, Any]) -> dict[str, str]:
    """Each controlled variable's unit, as the device declares it."""
    return {name: v["unit"] for name, v in manifest["controlled_variables"].items()}


def command_fields(command: SetpointCommand, manifest: dict[str, Any]) -> dict[str, Any]:
    """The device's view of an AutoLab command. Raises ValueError on incompatible units.

    Names the device doesn't declare are passed through unconverted. The firmware
    rejects them (``unknown_variable``), so the refusal comes from the device itself.
    """
    units = variable_units(manifest)
    setpoints = {
        name: float(value.to(units[name]).value if name in units else value.value)
        for name, value in command.setpoints.items()
    }
    return {
        "command_id": str(command.command_id),
        "experiment_id": str(command.experiment_id),
        "reactor_id": command.reactor_id,
        "setpoints": setpoints,
    }


def capabilities(
    controller_id: str, adapter_name: str, manifest: dict[str, Any]
) -> CultureControllerCapabilities:
    variables = {}
    for name, v in manifest["controlled_variables"].items():
        unit = v["unit"]
        variables[name] = ControlledVariable(
            unit=unit,
            range=QuantityRange(
                minimum=q(v["minimum"], unit),
                maximum=q(v["maximum"], unit),
                resolution=q(v["resolution"], interval_unit(unit)),
            ),
            tracked=bool(v["tracked"]),
        )
    units = variable_units(manifest)
    limits = {
        name: ProtectionLimit(
            minimum=None if "minimum" not in limit else q(limit["minimum"], units[name]),
            maximum=None if "maximum" not in limit else q(limit["maximum"], units[name]),
        )
        for name, limit in manifest["protection_limits"].items()
    }
    return CultureControllerCapabilities(
        controller_id=controller_id,
        device_type=manifest["device_type"],
        adapter_name=adapter_name,
        adapter_version=__version__,
        firmware_version=manifest["firmware_version"],
        reactor_ids=frozenset(manifest["reactor_ids"]),
        controlled_variables=variables,
        protection_limits=limits,
        resources=frozenset(manifest["resources"]),
        maximum_hold_duration=q(manifest["maximum_hold_h"], "hour"),
        is_simulated=bool(manifest["is_simulated"]),
        metadata={"model": manifest["model"], "seed": manifest["seed"]},
    )


def _quantities(values: dict[str, Any], units: dict[str, str]) -> dict[str, QuantityValue]:
    return {name: q(value, units[name]) for name, value in values.items()}


def acknowledgement(
    controller_id: str, ack: dict[str, Any], manifest: dict[str, Any]
) -> SetpointAcknowledgement:
    accepted = bool(ack["accepted"])
    return SetpointAcknowledgement(
        command_id=UUID(ack["command_id"]),
        controller_id=controller_id,
        reactor_id=ack["reactor_id"],
        accepted=accepted,
        applied=_quantities(ack["applied"], variable_units(manifest)) if accepted else None,
        rejection_code=ack.get("rejection_code"),
        rejection_message=ack.get("rejection_message"),
        acknowledged_at=ts(ack["acknowledged_at"]),
    )


def telemetry(
    controller_id: str, sample: dict[str, Any], manifest: dict[str, Any]
) -> EnvironmentTelemetry:
    units = variable_units(manifest)
    active = sample["active_command_id"]
    return EnvironmentTelemetry(
        controller_id=controller_id,
        reactor_id=sample["reactor_id"],
        sampled_at=ts(sample["sampled_at"]),
        applied=_quantities(sample["applied"], units),
        measured={
            name: None if value is None else q(value, units[name])
            for name, value in sample["measured"].items()
        },
        consumption=_quantities(sample["consumption"], manifest["resources"]),
        protection_tripped=bool(sample["protection_tripped"]),
        trip_count=int(sample["trip_count"]),
        faults=tuple(sample["faults"]),
        active_command_id=None if active is None else UUID(active),
    )


def safe_state(controller_id: str, ack: dict[str, Any]) -> SafeStateAcknowledgement:
    return SafeStateAcknowledgement(
        controller_id=controller_id,
        reactor_id=ack["reactor_id"],
        reason=ack["reason"],
        reached=bool(ack["reached"]),
        acknowledged_at=ts(ack["acknowledged_at"]),
        detail=ack.get("detail"),
    )


def od_sample(reading: dict[str, Any] | None) -> OdSample | None:
    if reading is None:
        return None
    return OdSample(
        od750=float(reading["od750"]),
        saturated=bool(reading["saturated"]),
        measured_at=ts(reading["measured_at"]),
        metadata={"hold_elapsed_h": reading["hold_elapsed_h"]},
    )


def calibration(
    sensor_id: str, manifest: dict[str, Any], valid_days: int = 365
) -> CalibrationRecord:
    """The probe's calibration, as the device states it (it survives device restarts)."""
    calibrated_at = ts(manifest["od750_calibrated_at"])
    return CalibrationRecord(
        calibration_id=uuid5(NAMESPACE_URL, f"algae-sim:{sensor_id}:{calibrated_at.isoformat()}"),
        sensor_id=sensor_id,
        measurement_type=MEASUREMENT_TYPE,
        calibrated_at=calibrated_at,
        expires_at=calibrated_at + timedelta(days=valid_days),
        method="simulated_linear_od750",
        reference_material="illustrative: OD750 = 2.0 x biomass (g/L)",
        coefficients={"od_per_g_per_l": Decimal("2.0")},
        created_at=calibrated_at,
    )
