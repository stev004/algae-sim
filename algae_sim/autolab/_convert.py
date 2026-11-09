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
from autonomous_lab.culture.models import CultureControllerCapabilities
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


def command_fields(command: SetpointCommand) -> dict[str, Any]:
    """The device's view of an AutoLab command. Raises ValueError on incompatible units."""
    return {
        "command_id": str(command.command_id),
        "experiment_id": str(command.experiment_id),
        "reactor_id": command.reactor_id,
        "temperature_c": float(command.temperature_setpoint.to(TEMP).value),
        "light_umol_m2_s": float(command.light_intensity.to(LIGHT).value),
    }


def capabilities(
    controller_id: str, adapter_name: str, manifest: dict[str, Any]
) -> CultureControllerCapabilities:
    t_min, t_max = manifest["temperature_setpoint_c"]
    l_min, l_max = manifest["light_umol_m2_s"]
    return CultureControllerCapabilities(
        controller_id=controller_id,
        device_type=manifest["device_type"],
        adapter_name=adapter_name,
        adapter_version=__version__,
        firmware_version=manifest["firmware_version"],
        reactor_ids=frozenset(manifest["reactor_ids"]),
        temperature_setpoint_range=QuantityRange(minimum=q(t_min, TEMP), maximum=q(t_max, TEMP)),
        light_intensity_range=QuantityRange(minimum=q(l_min, LIGHT), maximum=q(l_max, LIGHT)),
        maximum_hold_duration=q(manifest["maximum_hold_h"], "hour"),
        over_temperature_trip=q(manifest["over_temperature_trip_c"], TEMP),
        is_simulated=bool(manifest["is_simulated"]),
        metadata={"model": manifest["model"], "seed": manifest["seed"]},
    )


def acknowledgement(controller_id: str, ack: dict[str, Any]) -> SetpointAcknowledgement:
    accepted = bool(ack["accepted"])
    return SetpointAcknowledgement(
        command_id=UUID(ack["command_id"]),
        controller_id=controller_id,
        reactor_id=ack["reactor_id"],
        accepted=accepted,
        applied_temperature_setpoint=q(ack["applied_temperature_c"], TEMP) if accepted else None,
        applied_light_intensity=q(ack["applied_light_umol_m2_s"], LIGHT) if accepted else None,
        rejection_code=ack.get("rejection_code"),
        rejection_message=ack.get("rejection_message"),
        acknowledged_at=ts(ack["acknowledged_at"]),
    )


def telemetry(controller_id: str, sample: dict[str, Any]) -> EnvironmentTelemetry:
    measured = sample["measured_temperature_c"]
    active = sample["active_command_id"]
    return EnvironmentTelemetry(
        controller_id=controller_id,
        reactor_id=sample["reactor_id"],
        sampled_at=ts(sample["sampled_at"]),
        measured_temperature=None if measured is None else q(measured, TEMP),
        applied_temperature_setpoint=q(sample["applied_temperature_c"], TEMP),
        applied_light_intensity=q(sample["applied_light_umol_m2_s"], LIGHT),
        protection_tripped=bool(sample["protection_tripped"]),
        trip_count=int(sample["trip_count"]),
        cumulative_energy=q(sample["cumulative_energy_kwh"], "kilowatt_hour"),
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
