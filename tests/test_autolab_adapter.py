"""AutoLab sensor contract and closed-loop campaign. Skipped without the extra."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

pytest.importorskip("autonomous_lab")

from autonomous_lab.domain.quantities import QuantityValue
from autonomous_lab.hardware.sensors.contracts import ReadOnlySensor
from autonomous_lab.hardware.sensors.errors import (
    SensorCalibrationError,
    SensorCapabilityError,
    SensorConnectionError,
    SensorNotReadyError,
)
from autonomous_lab.hardware.sensors.registry import load_sensor_adapter

from algae_sim import Reactor, ReactorConfig
from algae_sim.autolab.campaign import run_campaign
from algae_sim.autolab.sensor import (
    ReactorBench,
    ReactorODSensor,
    default_calibration,
    od_request,
)
from algae_sim.instruments import OD750_PER_G_PER_L


def run(coro):
    return asyncio.run(coro)


def bench_with_reactor(hours=6.0):
    bench = ReactorBench()
    reactor = Reactor(ReactorConfig(batch_hours=hours))
    reactor.run()
    bench.add("r1", reactor)
    return bench, reactor


def make_sensor(bench, calibration="default"):
    cal = default_calibration("od-1") if calibration == "default" else calibration
    return ReactorODSensor(sensor_id="od-1", source=bench, calibration=cal)


def test_discovered_through_autolab_entry_point():
    sensor = load_sensor_adapter("algae_sim_od", sensor_id="od-ep", settings={})
    assert isinstance(sensor, ReadOnlySensor)
    assert sensor.sensor_id == "od-ep"


def test_valid_read_returns_identity_bound_evidence():
    bench, reactor = bench_with_reactor()

    async def go():
        sensor = make_sensor(bench)
        await sensor.open()
        await sensor.self_test()
        request = od_request(uuid4(), uuid4(), "r1")
        return request, await sensor.read(request)

    request, m = run(go())
    assert m.request_id == request.request_id and m.sample_id == "r1"
    assert m.source_evidence.is_mock is True  # simulated, never claimed as hardware
    assert m.source_evidence.calibration_id is not None
    assert float(m.raw_value) == pytest.approx(
        OD750_PER_G_PER_L * reactor.true_biomass_g_per_l, abs=0.05
    )
    assert m.measured_at.isoformat() == "2026-01-01T06:00:00+00:00"


def test_lifecycle_is_enforced():
    bench, _ = bench_with_reactor()

    async def go():
        sensor = make_sensor(bench)
        with pytest.raises(SensorConnectionError):
            await sensor.read(od_request(uuid4(), uuid4(), "r1"))
        await sensor.open()
        with pytest.raises(SensorNotReadyError):
            await sensor.read(od_request(uuid4(), uuid4(), "r1"))

    run(go())


def test_rejects_unsupported_requests():
    bench, _ = bench_with_reactor()

    async def go():
        sensor = make_sensor(bench)
        await sensor.open()
        await sensor.self_test()
        with pytest.raises(SensorCapabilityError):
            await sensor.read(od_request(uuid4(), uuid4(), "missing-reactor"))
        bad = od_request(uuid4(), uuid4(), "r1").model_copy(
            update={"wavelength": QuantityValue(value=Decimal(680), unit="nanometer")}
        )
        with pytest.raises(SensorCapabilityError):
            await sensor.read(bad)

    run(go())


def test_expired_calibration_blocks_reads():
    bench, _ = bench_with_reactor()

    async def go():
        sensor = make_sensor(bench, default_calibration("od-1", valid_days=1))
        await sensor.open()
        await sensor.self_test()
        with pytest.raises(SensorCalibrationError):
            await sensor.read(od_request(uuid4(), uuid4(), "r1"))

    run(go())


def test_campaign_is_reproducible_and_quarantines_faults():
    first = run(run_campaign(trials=5, seed=3, startup=3, faulty_trials=frozenset({1})))
    again = run(run_campaign(trials=5, seed=3, startup=3, faulty_trials=frozenset({1})))
    key = [(o.setpoint_c, o.light, o.od750, o.disposition) for o in first]
    assert key == [(o.setpoint_c, o.light, o.od750, o.disposition) for o in again]
    assert first[1].disposition == "qc_failed"
    assert "temperature_out_of_control" in (first[1].qc_reason or "")
    assert all(o.disposition == "observed" for i, o in enumerate(first) if i != 1)
