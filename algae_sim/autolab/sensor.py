"""The reactor's optical-density probe behind AutoLab's read-only sensor contract.

The adapter only measures. It does not know campaign objectives or optimiser state,
it cannot change setpoints, and it never reads hidden true biomass: it asks the
simulated ``OpticalDensitySensor`` for a reading, exactly as a vendor adapter would
ask real hardware. Evidence is marked ``is_mock=True`` because no hardware ran.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from autonomous_lab.domain.protocols import QuantityRange
from autonomous_lab.domain.quantities import QuantityValue
from autonomous_lab.hardware.sensors.calibration import (
    CalibrationRecord,
    CalibrationReference,
    validate_calibration,
)
from autonomous_lab.hardware.sensors.contracts import (
    ConnectionStatus,
    MeasurementRequest,
    RawSensorMeasurement,
    ReadOnlySensor,
    SelfTestCheck,
    SelfTestResult,
    SelfTestStatus,
    SensorCapabilities,
    SensorConnection,
    SensorSourceEvidence,
    WavelengthCapabilities,
)
from autonomous_lab.hardware.sensors.errors import (
    SensorCalibrationError,
    SensorCapabilityError,
    SensorConnectionError,
    SensorNotReadyError,
)

from algae_sim import __version__
from algae_sim.reactor import DEFAULT_START_TIME, Reactor

MEASUREMENT_TYPE = "optical_density"
UNIT = "dimensionless"
WAVELENGTH_NM = 750
ADAPTER_NAME = "algae-sim-od750"


@dataclass(frozen=True, slots=True)
class OdSample:
    """One OD750 reading as the instrument reports it, stamped with the device's time."""

    od750: float
    saturated: bool
    measured_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class OdSource(Protocol):
    """Where the probe's readings come from: an in-process reactor or a remote device.

    Returns None if there is no culture in that reactor; a remote source may return an
    awaitable instead. May raise a ``SensorError`` (e.g. ``SensorConnectionError``
    when a remote device is unreachable).
    """

    def sample_od(
        self, reactor_id: str
    ) -> OdSample | Awaitable[OdSample | None] | None: ...


class ReactorBench:
    """The simulated 'lab bench': reactors addressable by sample ID."""

    def __init__(self) -> None:
        self._reactors: dict[str, Reactor] = {}

    def add(self, reactor_id: str, reactor: Reactor) -> None:
        if reactor_id in self._reactors:
            raise ValueError(f"reactor {reactor_id!r} already on the bench")
        self._reactors[reactor_id] = reactor

    def get(self, reactor_id: str) -> Reactor | None:
        return self._reactors.get(reactor_id)

    def remove(self, reactor_id: str) -> None:
        self._reactors.pop(reactor_id, None)

    def sample_od(self, reactor_id: str) -> OdSample | None:
        reactor = self._reactors.get(reactor_id)
        if reactor is None:
            return None
        od = reactor.read_od750()
        return OdSample(
            od750=od,
            saturated=od >= reactor.od_sensor.max_od,
            measured_at=DEFAULT_START_TIME + timedelta(seconds=reactor.elapsed_s),
            metadata={"reactor_elapsed_s": reactor.elapsed_s},
        )


def default_calibration(
    sensor_id: str, *, valid_days: int = 365, calibrated_at: datetime | None = None
) -> CalibrationRecord:
    if calibrated_at is None:
        calibrated_at = DEFAULT_START_TIME - timedelta(days=1)
    return CalibrationRecord(
        calibration_id=uuid5(NAMESPACE_URL, f"algae-sim:{sensor_id}:calibration"),
        sensor_id=sensor_id,
        measurement_type=MEASUREMENT_TYPE,
        calibrated_at=calibrated_at,
        expires_at=calibrated_at + timedelta(days=valid_days),
        method="simulated_linear_od750",
        reference_material="illustrative: OD750 = 2.0 x biomass (g/L)",
        coefficients={"od_per_g_per_l": Decimal("2.0")},
        created_at=calibrated_at,
    )


class ReactorODSensor(ReadOnlySensor):
    def __init__(
        self,
        *,
        sensor_id: str,
        source: OdSource,
        calibration: CalibrationRecord | None,
        clock: Callable[[], datetime] | None = None,
        transport: str = "in-process-simulation",
    ) -> None:
        """Readings are stamped by ``source``; ``clock`` only stamps connection events."""
        if not sensor_id:
            raise ValueError("sensor_id must not be empty")
        self._sensor_id = sensor_id
        self._source = source
        self._calibration = calibration
        self._clock = clock or (lambda: DEFAULT_START_TIME)
        self._transport = transport
        self._connection: SensorConnection | None = None
        self._self_test: SelfTestResult | None = None

    @property
    def sensor_id(self) -> str:
        return self._sensor_id

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    async def open(self) -> SensorConnection:
        if self._connection is None:
            self._connection = SensorConnection(
                sensor_id=self.sensor_id,
                status=ConnectionStatus.OPEN,
                changed_at=self._clock(),
                transport_metadata={"transport": self._transport, "is_mock": True},
            )
            self._self_test = None
        return self._connection

    async def close(self) -> SensorConnection:
        if self._connection is None:
            raise SensorConnectionError("cannot close a sensor that is not connected")
        closed = SensorConnection(
            session_id=self._connection.session_id,
            sensor_id=self.sensor_id,
            status=ConnectionStatus.CLOSED,
            changed_at=self._clock(),
            transport_metadata=self._connection.transport_metadata,
        )
        self._connection = None
        self._self_test = None
        return closed

    async def capability_manifest(self) -> SensorCapabilities:
        calibrations = (
            ()
            if self._calibration is None
            else (CalibrationReference.from_record(self._calibration, at=self._clock()),)
        )
        return SensorCapabilities(
            sensor_id=self.sensor_id,
            sensor_type="simulated_od_probe",
            adapter_name=ADAPTER_NAME,
            adapter_version=__version__,
            firmware_version="sim",
            measurement_types=frozenset({MEASUREMENT_TYPE}),
            measurement_ranges={
                MEASUREMENT_TYPE: QuantityRange(
                    minimum=QuantityValue(value=Decimal(0), unit=UNIT),
                    maximum=QuantityValue(value=Decimal(4), unit=UNIT),
                )
            },
            resolutions={MEASUREMENT_TYPE: QuantityValue(value=Decimal("0.001"), unit=UNIT)},
            wavelength=WavelengthCapabilities(
                discrete_wavelengths=(
                    QuantityValue(value=Decimal(WAVELENGTH_NM), unit="nanometer"),
                )
            ),
            calibration_required=True,
            calibrations=calibrations,
            metadata={"simulated": True, "is_mock": True},
        )

    async def self_test(self) -> SelfTestResult:
        if self._connection is None:
            raise SensorConnectionError()
        started = self._clock()
        has_calibration = self._calibration is not None
        result = SelfTestResult(
            sensor_id=self.sensor_id,
            status=SelfTestStatus.PASSED if has_calibration else SelfTestStatus.FAILED,
            started_at=started,
            completed_at=self._clock(),
            checks=(
                SelfTestCheck(name="simulation_link", passed=True),
                SelfTestCheck(
                    name="calibration_present",
                    passed=has_calibration,
                    message=None if has_calibration else "no calibration loaded",
                ),
            ),
            metadata={"adapter": ADAPTER_NAME, "is_mock": True},
        )
        self._self_test = result
        return result

    async def read(self, request: MeasurementRequest) -> RawSensorMeasurement:
        connection = self._require_ready()
        manifest = await self.capability_manifest()
        self._validate_request(request)
        sample = self._source.sample_od(request.sample_id)
        if inspect.isawaitable(sample):
            sample = await sample
        if sample is None:
            raise SensorCapabilityError(f"no culture in reactor {request.sample_id!r}")
        measured_at = sample.measured_at
        check = validate_calibration(
            self._calibration,
            sensor_id=self.sensor_id,
            measurement_type=MEASUREMENT_TYPE,
            at=measured_at,
        )
        if not check.valid:
            issue = check.issues[0]
            raise SensorCalibrationError(issue.code, issue.message)
        assert self._self_test is not None
        evidence = SensorSourceEvidence(
            sensor_id=self.sensor_id,
            connection_session_id=connection.session_id,
            self_test_id=self._self_test.self_test_id,
            adapter_name=ADAPTER_NAME,
            adapter_version=__version__,
            firmware_version="sim",
            capability_sha256=manifest.sha256,
            calibration_id=check.calibration_id,
            calibration_sha256=check.calibration_sha256,
            is_mock=True,
            acquired_at=measured_at,
            metadata={"simulated": True, **sample.metadata},
        )
        return RawSensorMeasurement(
            request_id=request.request_id,
            experiment_id=request.experiment_id,
            run_id=request.run_id,
            sample_id=request.sample_id,
            measurement_type=MEASUREMENT_TYPE,
            raw_value=Decimal(str(sample.od750)),
            unit=UNIT,
            wavelength=request.wavelength,
            measured_at=measured_at,
            source_evidence=evidence,
            metadata={"saturated": sample.saturated},
        )

    def _require_ready(self) -> SensorConnection:
        if self._connection is None:
            raise SensorConnectionError()
        if self._self_test is None or self._self_test.status is not SelfTestStatus.PASSED:
            raise SensorNotReadyError()
        return self._connection

    @staticmethod
    def _validate_request(request: MeasurementRequest) -> None:
        if request.measurement_type != MEASUREMENT_TYPE:
            raise SensorCapabilityError(
                f"unsupported measurement type {request.measurement_type!r}"
            )
        if request.expected_unit != UNIT:
            raise SensorCapabilityError(f"expected unit must be {UNIT!r}")
        if request.wavelength is None:
            raise SensorCapabilityError("this optical measurement requires a wavelength")
        if request.wavelength.to("nanometer").value != WAVELENGTH_NM:
            raise SensorCapabilityError(f"only {WAVELENGTH_NM} nm is supported")


def od_request(experiment_id: UUID, run_id: UUID, reactor_id: str) -> MeasurementRequest:
    return MeasurementRequest(
        experiment_id=experiment_id,
        run_id=run_id,
        sample_id=reactor_id,
        measurement_type=MEASUREMENT_TYPE,
        expected_unit=UNIT,
        wavelength=QuantityValue(value=Decimal(WAVELENGTH_NM), unit="nanometer"),
        requested_at=DEFAULT_START_TIME,
    )


DEFAULT_BENCH = ReactorBench()


def create_sensor(*, sensor_id: str, settings: Mapping[str, object]) -> ReactorODSensor:
    """Entry-point factory used by AutoLab's ``SensorAdapterRegistry``."""
    del settings  # no adapter-owned settings yet
    return ReactorODSensor(
        sensor_id=sensor_id,
        source=DEFAULT_BENCH,
        calibration=default_calibration(sensor_id),
    )
