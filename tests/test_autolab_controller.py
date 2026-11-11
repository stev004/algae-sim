"""The simulated reactor as an AutoLab culture controller (culture contract v2)."""

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

pytest.importorskip("autonomous_lab.culture")

from autonomous_lab.culture import (
    ControllerError,
    CultureControllerRegistry,
    CultureEnvironmentController,
    IntegratedCultureSensor,
    SetpointCommand,
    SteppedClock,
)
from autonomous_lab.domain.quantities import QuantityValue
from autonomous_lab.hardware.sensors.contracts import ReadOnlySensor

from algae_sim.autolab.controller import LIGHT, TEMP, create_controller
from algae_sim.autolab.sensor import od_request

PH = "dimensionless"

START = datetime(2026, 11, 1, 8, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def q(value, unit):
    return QuantityValue(value=Decimal(str(value)), unit=unit)


def command(setpoint=30, light=150, ph=7.5, reactor="reactor-1", experiment_id=None, **extra):
    setpoints = {"temperature": q(setpoint, TEMP), "light_intensity": q(light, LIGHT)}
    if ph is not None:
        setpoints["ph"] = q(ph, PH)
    return SetpointCommand(
        experiment_id=experiment_id or uuid4(),
        run_id=uuid4(),
        reactor_id=reactor,
        setpoints={**setpoints, **extra},
        protocol_hash="0" * 64,
        issued_at=START,
    )


def hourly_telemetry(clock, ctrl, hours, reactor="reactor-1"):
    async def go():
        samples = []
        for hour in range(1, hours + 1):
            await clock.sleep_until(START + timedelta(hours=hour))
            samples.append(await ctrl.read_telemetry(reactor))
        return samples

    return run(go())


def make(**settings):
    clock = SteppedClock(START)
    return clock, create_controller(controller_id="ctrl", settings=settings, clock=clock)


def run(coro):
    return asyncio.run(coro)


def test_discovered_through_autolab_registry():
    clock = SteppedClock(START)
    controller = CultureControllerRegistry().create(
        "algae-sim-reactor", controller_id="ctrl-9", settings={"seed": 1}, clock=clock
    )
    assert isinstance(controller, CultureEnvironmentController)
    assert isinstance(controller, IntegratedCultureSensor)
    assert isinstance(controller.culture_sensor(), ReadOnlySensor)
    manifest = run(controller.capability_manifest())
    assert manifest.is_simulated and manifest.controller_id == "ctrl-9"
    assert manifest.reactor_ids == {"reactor-1", "reactor-2", "reactor-3"}


def test_manifest_declares_variables_limits_and_resources():
    _, ctrl = make()
    m = run(ctrl.capability_manifest())
    assert set(m.controlled_variables) == {"temperature", "light_intensity", "ph"}
    assert set(m.tracked_variables) == {"temperature", "ph"}
    temperature = m.controlled_variables["temperature"].range
    assert (temperature.minimum, temperature.maximum) == (q(20.0, TEMP), q(36.0, TEMP))
    assert m.controlled_variables["ph"].range.resolution == q(0.05, PH)
    assert m.protection_limits["temperature"].maximum == q(39.0, TEMP)
    assert m.protection_limits["ph"].minimum == q(5.5, PH)
    assert m.protection_limits["ph"].maximum == q(9.8, PH)
    assert m.resources == {"energy", "co2"}


def test_refuses_commands_outside_its_own_limits():
    _, ctrl = make()
    too_hot = run(ctrl.apply_setpoints(command(setpoint=37)))
    assert not too_hot.accepted and too_hot.rejection_code == "setpoint_out_of_device_range"
    too_acid = run(ctrl.apply_setpoints(command(ph=5.9)))
    assert not too_acid.accepted and too_acid.rejection_code == "setpoint_out_of_device_range"
    # The command must name exactly the device's variables: no more, no fewer.
    missing = run(ctrl.apply_setpoints(command(ph=None)))
    assert not missing.accepted and missing.rejection_code == "missing_variable"
    extra = run(ctrl.apply_setpoints(command(stirring=q(200, "1 / minute"))))
    assert not extra.accepted and extra.rejection_code == "unknown_variable"
    unknown = run(ctrl.apply_setpoints(command(reactor="reactor-9")))
    assert not unknown.accepted and unknown.rejection_code == "unknown_reactor"
    with pytest.raises(ControllerError):
        run(ctrl.read_telemetry("reactor-1"))  # no batch was started


def test_hold_follows_autolab_clock_and_holds_setpoint():
    clock, ctrl = make()
    cmd = command(setpoint=30, light=150, ph=7.2)
    ack = run(ctrl.apply_setpoints(cmd))
    assert ack.accepted and ack.applied == cmd.setpoints  # QC compares these exactly
    samples = hourly_telemetry(clock, ctrl, 12)
    assert samples[-1].sampled_at == START + timedelta(hours=12)
    assert all(set(s.measured) == {"temperature", "ph"} for s in samples)
    after_warmup = samples[3:]
    assert all(abs(float(s.measured["temperature"].value) - 30.0) < 1.0 for s in after_warmup)
    assert all(abs(float(s.measured["ph"].value) - 7.2) < 0.1 for s in after_warmup)
    for resource in ("energy", "co2"):
        used = [s.consumption[resource].value for s in samples]
        assert used == sorted(used) and used[-1] > 0
    assert not any(s.protection_tripped or s.faults for s in samples)


def test_lower_ph_setpoint_costs_more_co2():
    """CO2 escapes to the air faster the further pH is held below air equilibrium."""

    def co2_after_12h(ph):
        clock, ctrl = make()
        run(ctrl.apply_setpoints(command(ph=ph)))
        return hourly_telemetry(clock, ctrl, 12)[-1].consumption["co2"].value

    assert co2_after_12h(6.5) > 2 * co2_after_12h(8.0)


def test_stuck_open_co2_valve_trips_ph_protection():
    clock, ctrl = make(co2_valve_faults={"reactor-1": [0]}, co2_valve_fault_after_h=1.0)
    run(ctrl.apply_setpoints(command(ph=7.0)))
    samples = hourly_telemetry(clock, ctrl, 12)
    tripped = [s for s in samples if s.protection_tripped]
    assert tripped and "ph_low_trip" in tripped[0].faults
    assert "co2_valve_not_responding" in samples[-1].faults
    # The shutoff stops the overdose: pH never falls far below the 5.5 trip.
    assert min(float(s.measured["ph"].value) for s in samples) > 5.3


def test_integrated_od_probe_reads_growing_culture():
    clock, ctrl = make()
    sensor = ctrl.culture_sensor()

    async def go():
        await ctrl.apply_setpoints(command())
        await sensor.open()
        await sensor.self_test()
        readings = []
        for hour in (0, 12, 24, 36):
            await clock.sleep_until(START + timedelta(hours=hour))
            m = await sensor.read(od_request(uuid4(), uuid4(), "reactor-1"))
            readings.append(m)
        return readings

    readings = run(go())
    values = [float(m.raw_value) for m in readings]
    assert values == sorted(values) and values[-1] > 3 * values[0]
    assert readings[-1].measured_at == START + timedelta(hours=36)
    assert all(m.source_evidence.is_mock for m in readings)


def test_safe_state_turns_heater_and_lights_off():
    clock, ctrl = make()

    async def go():
        await ctrl.apply_setpoints(command(setpoint=32, ph=6.5))
        await clock.sleep_until(START + timedelta(hours=4))
        ack = await ctrl.enter_safe_state("reactor-1", "hold complete")
        at_safe = await ctrl.read_telemetry("reactor-1")
        await clock.sleep_until(START + timedelta(hours=8))
        return ack, at_safe, await ctrl.read_telemetry("reactor-1")

    ack, at_safe, after = run(go())
    assert ack.reached and "CO2" in ack.detail
    assert after.applied["light_intensity"].value == 0
    assert float(after.measured["temperature"].value) < 22.0  # cooled towards 20 °C room
    assert after.consumption["co2"] == at_safe.consumption["co2"]  # no more CO2 dosed
    assert float(after.measured["ph"].value) > 6.8  # pH drifts up once dosing stops


def test_injected_heater_fault_is_reported_as_attested_fault():
    clock, ctrl = make(heater_faults={"reactor-1": [0]}, heater_fault_after_h=2.0)

    async def go():
        await ctrl.apply_setpoints(command(setpoint=30))
        await clock.sleep_until(START + timedelta(hours=6))
        return await ctrl.read_telemetry("reactor-1")

    sample = run(go())
    assert "heater_not_responding" in sample.faults
    assert float(sample.measured["temperature"].value) < 25.0


def test_same_experiment_same_culture():
    experiment = uuid4()

    def final_od():
        clock, ctrl = make(seed=3)

        async def go():
            await ctrl.apply_setpoints(command(experiment_id=experiment))
            await clock.sleep_until(START + timedelta(hours=24))
            sensor = ctrl.culture_sensor()
            await sensor.open()
            await sensor.self_test()
            return (await sensor.read(od_request(experiment, uuid4(), "reactor-1"))).raw_value

        return run(go())

    assert final_od() == final_od()


def test_unknown_settings_rejected():
    with pytest.raises(ControllerError):
        create_controller(controller_id="c", settings={"sead": 1}, clock=SteppedClock(START))
    with pytest.raises(ControllerError):
        make(heater_faults={"reactor-9": [0]})


def test_reports_the_command_it_holds_until_safe_state():
    clock, ctrl = make()

    async def go():
        cmd = command()
        await ctrl.apply_setpoints(cmd)
        await clock.sleep_until(START + timedelta(hours=1))
        holding = await ctrl.read_telemetry("reactor-1")
        await ctrl.enter_safe_state("reactor-1", "hold complete")
        released = await ctrl.read_telemetry("reactor-1")
        return cmd, holding, released

    cmd, holding, released = run(go())
    assert holding.active_command_id == cmd.command_id
    assert released.active_command_id is None


def od_after(ctrl, clock, reactor_id, experiment, hours):
    async def go():
        await clock.sleep_until(START + timedelta(hours=hours))
        sensor = ctrl.culture_sensor()
        if not sensor.is_open:
            await sensor.open()
            await sensor.self_test()
        m = await sensor.read(od_request(experiment, uuid4(), reactor_id))
        assert m.sample_id == reactor_id
        return float(m.raw_value)

    return run(go())


def test_replicates_are_independent_but_reproducible():
    experiment = uuid4()

    def finals():
        clock, ctrl = make(seed=3)
        for reactor in ("reactor-1", "reactor-2"):
            run(ctrl.apply_setpoints(command(reactor=reactor, experiment_id=experiment)))
        return [od_after(ctrl, clock, r, experiment, 36) for r in ("reactor-1", "reactor-2")]

    first, again = finals(), finals()
    assert first == again  # same experiment, same cultures
    assert first[0] != first[1]  # but each replicate is its own culture
    assert abs(first[0] - first[1]) < 0.1  # ...grown under the same conditions


def test_faults_hit_only_the_named_reactor_and_hold():
    experiment = uuid4()
    clock, ctrl = make(culture_crashes={"reactor-3": [1]}, culture_crash_after_h=6.0)
    for _hold in range(2):  # hold 0 is normal; hold 1 restarts each culture at "now"
        for reactor in ("reactor-1", "reactor-3"):
            run(ctrl.apply_setpoints(command(reactor=reactor, experiment_id=experiment)))
    # Second hold on reactor-3 crashes after 6 h; reactor-1 keeps growing.
    od_12 = {r: od_after(ctrl, clock, r, experiment, 12) for r in ("reactor-1", "reactor-3")}
    od_24 = {r: od_after(ctrl, clock, r, experiment, 24) for r in ("reactor-1", "reactor-3")}
    assert od_24["reactor-1"] > od_12["reactor-1"]
    assert od_24["reactor-3"] < od_12["reactor-3"]


def test_full_autolab_culture_campaign(tmp_path):
    """End to end through AutoLab's own CLI: create, run 6 cultures, report."""
    autolab = Path(sys.executable).parent / "autolab"
    config = yaml.safe_load((ROOT / "configs/culture-algae-sim.yaml").read_text())
    config["campaign"]["max_experiments"] = 5
    config["optimization"]["startup_experiments"] = 3
    # Experiment 1: one replicate fails (still counts). Experiment 3: two fail (QC fails).
    config["execution"]["controller"]["settings"].update(
        heater_faults={"reactor-1": [1, 3], "reactor-2": [3]},
        culture_crashes={},
        co2_valve_faults={},
    )
    (tmp_path / "campaign.yaml").write_text(yaml.safe_dump(config))
    common = [
        "--database", str(tmp_path / "db.sqlite3"),
        "--policy", str(ROOT / "configs/culture-policy.yaml"),
    ]

    def cli(*args):
        return subprocess.run(
            [str(autolab), *args], check=True, capture_output=True, text=True, cwd=tmp_path
        ).stdout

    cli("init", "--database", str(tmp_path / "db.sqlite3"))
    campaign = cli("culture", "create", "campaign.yaml", *common, "--id-only").strip()
    status = cli("culture", "run", campaign, *common)
    assert "state: completed" in status
    assert "qc_failed: 1" in status  # only experiment 3, where 2 of 3 replicates failed
    assert "observed: 4" in status
    report = json.loads(cli("culture", "report", campaign, *common))
    assert report["evidence_origins"]["controller_adapter"] == "algae-sim-reactor"
    origins = report["evidence_origins"]
    assert origins["od_readings"] == origins["od_readings_from_simulated_or_mock_sensors"] > 0
