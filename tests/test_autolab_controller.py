"""The simulated reactor as an AutoLab stage-6 culture controller."""

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

START = datetime(2026, 11, 1, 8, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def q(value, unit):
    return QuantityValue(value=Decimal(str(value)), unit=unit)


def command(setpoint=30, light=150, reactor="reactor-1", experiment_id=None):
    return SetpointCommand(
        experiment_id=experiment_id or uuid4(),
        run_id=uuid4(),
        reactor_id=reactor,
        temperature_setpoint=q(setpoint, TEMP),
        light_intensity=q(light, LIGHT),
        protocol_hash="0" * 64,
        issued_at=START,
    )


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


def test_refuses_commands_outside_its_own_limits():
    _, ctrl = make()
    too_hot = run(ctrl.apply_setpoints(command(setpoint=37)))
    assert not too_hot.accepted and too_hot.rejection_code == "setpoint_out_of_device_range"
    unknown = run(ctrl.apply_setpoints(command(reactor="reactor-9")))
    assert not unknown.accepted and unknown.rejection_code == "unknown_reactor"
    with pytest.raises(ControllerError):
        run(ctrl.read_telemetry("reactor-1"))  # no batch was started


def test_hold_follows_autolab_clock_and_holds_setpoint():
    clock, ctrl = make()

    async def go():
        ack = await ctrl.apply_setpoints(command(setpoint=30, light=150))
        assert ack.accepted and ack.applied_temperature_setpoint == q(30.0, TEMP)
        samples = []
        for hour in range(1, 13):
            await clock.sleep_until(START + timedelta(hours=hour))
            samples.append(await ctrl.read_telemetry("reactor-1"))
        return samples

    samples = run(go())
    assert samples[-1].sampled_at == START + timedelta(hours=12)
    after_warmup = [float(s.measured_temperature.value) for s in samples[3:]]
    assert all(abs(t - 30.0) < 1.0 for t in after_warmup)
    energy = [s.cumulative_energy.value for s in samples]
    assert energy == sorted(energy) and energy[-1] > 0
    assert not any(s.protection_tripped or s.faults for s in samples)


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
        await ctrl.apply_setpoints(command(setpoint=32))
        await clock.sleep_until(START + timedelta(hours=4))
        ack = await ctrl.enter_safe_state("reactor-1", "hold complete")
        await clock.sleep_until(START + timedelta(hours=8))
        return ack, await ctrl.read_telemetry("reactor-1")

    ack, after = run(go())
    assert ack.reached
    assert after.applied_light_intensity.value == 0
    assert float(after.measured_temperature.value) < 22.0  # cooled towards 20 °C room


def test_injected_heater_fault_is_reported_as_attested_fault():
    clock, ctrl = make(heater_faults={"reactor-1": [0]}, heater_fault_after_h=2.0)

    async def go():
        await ctrl.apply_setpoints(command(setpoint=30))
        await clock.sleep_until(START + timedelta(hours=6))
        return await ctrl.read_telemetry("reactor-1")

    sample = run(go())
    assert "heater_not_responding" in sample.faults
    assert float(sample.measured_temperature.value) < 25.0


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
        heater_faults={"reactor-1": [1, 3], "reactor-2": [3]}, culture_crashes={}
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
