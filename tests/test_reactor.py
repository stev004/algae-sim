"""Whole-reactor behaviour, faults, reproducibility and run records."""

import json

import pytest

from algae_sim import ReactorConfig, run_batch
from algae_sim.reactor import DEFAULT_START_TIME
from algae_sim.records import write_run

SHORT = {"batch_hours": 12.0}


def after_warmup(result, hours=3.0):
    return [s for s in result.telemetry if s.elapsed_s >= hours * 3600]


def test_holds_setpoint_after_warmup():
    result = run_batch(ReactorConfig(temperature_setpoint_c=30.0, **SHORT))
    temps = [s.true_temperature_c for s in after_warmup(result)]
    assert min(temps) > 29.0 and max(temps) < 31.0
    assert result.trip_count == 0


def test_same_seed_is_identical_and_different_seed_differs():
    a = run_batch(ReactorConfig(seed=4, **SHORT))
    b = run_batch(ReactorConfig(seed=4, **SHORT))
    c = run_batch(ReactorConfig(seed=5, **SHORT))
    assert a.telemetry == b.telemetry
    assert a.telemetry != c.telemetry


def test_controller_acts_on_measurement_not_truth():
    # A sensor that reads 2 °C high makes the controller hold the vessel ~2 °C low.
    result = run_batch(
        ReactorConfig(temperature_setpoint_c=30.0, temperature_sensor_offset_c=2.0, **SHORT)
    )
    true_mean = sum(s.true_temperature_c for s in after_warmup(result)) / len(
        after_warmup(result)
    )
    assert true_mean == pytest.approx(28.0, abs=0.5)


def test_heater_failure_cools_to_ambient_and_slows_growth():
    healthy = run_batch(ReactorConfig(**SHORT))
    failed = run_batch(ReactorConfig(heater_fails_after_h=2.0, **SHORT))
    assert failed.telemetry[-1].true_temperature_c == pytest.approx(20.0, abs=0.5)
    assert failed.telemetry[-1].heater_command == 1.0  # controller still asks for heat
    assert failed.telemetry[-1].heater_applied == 0.0  # but nothing is applied
    assert failed.final_true_biomass_g_per_l < healthy.final_true_biomass_g_per_l


def test_sensor_dropouts_are_counted_and_fail_safe():
    result = run_batch(ReactorConfig(temperature_dropout_probability=0.2, **SHORT))
    assert result.missing_temperature_readings > 0
    assert result.max_true_temperature_c < 32.0


def test_growth_is_best_near_the_optimum():
    def od(setpoint, light):
        return run_batch(
            ReactorConfig(temperature_setpoint_c=setpoint, light_umol_m2_s=light)
        ).final_true_biomass_g_per_l

    assert od(30, 150) > od(22, 150)
    assert od(30, 150) > od(30, 400)
    assert od(30, 150) > od(36, 150)


def test_energy_rises_with_setpoint():
    assert run_batch(ReactorConfig(temperature_setpoint_c=34.0, **SHORT)).heater_energy_wh > (
        run_batch(ReactorConfig(temperature_setpoint_c=24.0, **SHORT)).heater_energy_wh
    )


def test_run_record_files(tmp_path):
    config = ReactorConfig(**SHORT)
    result = run_batch(config)
    write_run(
        tmp_path,
        device_id="reactor-1",
        start_time=DEFAULT_START_TIME,
        config=config.as_dict(),
        summary=result.summary(),
        telemetry=result.telemetry,
    )
    run = json.loads((tmp_path / "run.json").read_text())
    assert run["config"]["seed"] == 0
    lines = (tmp_path / "telemetry.jsonl").read_text().splitlines()
    assert len(lines) == len(result.telemetry)
    first, last = json.loads(lines[0]), json.loads(lines[-1])
    assert first["schema_version"] == "1" and first["sequence"] == 0
    assert first["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert set(last) == {
        "schema_version", "device_id", "sequence", "timestamp",
        "measurements", "control", "simulator_truth",
    }


@pytest.mark.parametrize(
    "kwargs", [{"batch_hours": 0}, {"dt_s": 5000}, {"telemetry_interval_s": 1}]
)
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        ReactorConfig(**kwargs)
