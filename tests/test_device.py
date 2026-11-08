"""Sensors, actuator and controller: the device-side boundary."""

import random

import pytest

from algae_sim.control import HysteresisController
from algae_sim.instruments import Heater, OpticalDensitySensor, TemperatureSensor


class TestSensors:
    def test_temperature_reading_is_quantised_noisy_and_offset(self):
        sensor = TemperatureSensor(offset_c=2.0, rng=random.Random(3))
        readings = [sensor.read(25.0) for _ in range(200)]
        assert all(r is not None and abs(r * 10 - round(r * 10)) < 1e-9 for r in readings)
        mean = sum(readings) / len(readings)  # type: ignore[arg-type]
        assert mean == pytest.approx(27.0, abs=0.05)

    def test_same_seed_same_readings(self):
        a = TemperatureSensor(rng=random.Random(9))
        b = TemperatureSensor(rng=random.Random(9))
        assert [a.read(25.0) for _ in range(20)] == [b.read(25.0) for _ in range(20)]

    def test_dropout_returns_none(self):
        assert TemperatureSensor(dropout_probability=1.0).read(25.0) is None

    def test_od_sensor_saturates_and_never_negative(self):
        sensor = OpticalDensitySensor(noise_sd_od=0.0)
        assert sensor.read(10.0) == sensor.max_od
        assert sensor.read(0.0) == 0.0
        assert sensor.read(0.25) == pytest.approx(0.5)

    def test_failed_heater_applies_nothing(self):
        assert Heater().apply(1.0) == 1.0
        assert Heater(failed=True).apply(1.0) == 0.0


class TestController:
    def test_hysteresis_band(self):
        c = HysteresisController(setpoint_c=30.0, band_c=0.3)
        assert c.decide(29.5) == 1.0
        assert c.decide(30.0) == 1.0  # inside the band: keep previous decision
        assert c.decide(30.4) == 0.0
        assert c.decide(30.0) == 0.0
        assert c.decide(29.7) == 1.0

    def test_missing_reading_fails_safe(self):
        c = HysteresisController(setpoint_c=30.0)
        c.decide(25.0)
        assert c.decide(None) == 0.0
        assert c.missing_reading_count == 1

    def test_over_temperature_trip_latches_until_cool(self):
        c = HysteresisController(setpoint_c=30.0, trip_c=39.0, trip_reset_band_c=2.0)
        assert c.decide(39.2) == 0.0 and c.tripped
        assert c.decide(37.5) == 0.0  # still latched
        assert c.decide(36.5) == 0.0 and not c.tripped  # reset, but above setpoint
        assert c.decide(29.5) == 1.0  # normal control resumes
        assert c.trip_count == 1

    def test_setpoint_too_close_to_trip_rejected(self):
        with pytest.raises(ValueError):
            HysteresisController(setpoint_c=38.9, trip_c=39.0)
