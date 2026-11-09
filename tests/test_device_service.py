"""The reactor bank as a device service, and AutoLab's network adapter to it."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from algae_sim.device.core import DeviceCore, DeviceError, DeviceSettings
from algae_sim.device.service import DeviceService, serve

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
from autonomous_lab.hardware.sensors.errors import SensorConnectionError

from algae_sim.autolab.remote import (
    NoReply,
    RemoteReactorController,
    RemoteSettings,
    Unreachable,
    create_remote_controller,
)
from algae_sim.autolab.sensor import od_request

START = datetime(2026, 11, 1, 8, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def q(value, unit):
    return QuantityValue(value=Decimal(str(value)), unit=unit)


def command(reactor="reactor-1", setpoint=30, light=150, experiment_id=None):
    return SetpointCommand(
        experiment_id=experiment_id or uuid4(),
        run_id=uuid4(),
        reactor_id=reactor,
        temperature_setpoint=q(setpoint, "degree_Celsius"),
        light_intensity=q(light, "micromole / meter ** 2 / second"),
        protocol_hash="0" * 64,
        issued_at=START,
    )


class RunningDevice:
    """A device service on an ephemeral port in a background thread."""

    def __init__(self, service: DeviceService) -> None:
        self.service = service
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=serve, args=(service,), kwargs={"port": 0, "ready": self._on_ready},
            daemon=True,
        )

    def _on_ready(self, server) -> None:
        self.server = server
        self._ready.set()

    def __enter__(self) -> "RunningDevice":
        self._thread.start()
        assert self._ready.wait(5)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self._thread.join(5)


def device(speed=0.0, state_dir=None, **settings):
    return RunningDevice(
        DeviceService(
            DeviceSettings.from_mapping(settings), speed=speed, state_dir=state_dir,
            start_time=START,
        )
    )


def run(coro):
    return asyncio.run(coro)


# -- firmware and service -----------------------------------------------------------


def test_repeated_command_is_applied_once():
    core = DeviceCore(DeviceSettings(), start_time=START)
    fields = dict(
        command_id="c1", experiment_id="e1", reactor_id="reactor-1",
        temperature_c=30.0, light_umol_m2_s=150.0,
    )
    first = core.apply_setpoints(**fields)
    again = core.apply_setpoints(**fields)
    assert first["accepted"] and again["duplicate"] is True
    assert core.holds["reactor-1"] == 1  # one batch, not two


def test_state_survives_a_service_restart(tmp_path):
    service = DeviceService(DeviceSettings(), state_dir=tmp_path, start_time=START)
    service.core.apply_setpoints(
        command_id="c1", experiment_id="e1", reactor_id="reactor-2",
        temperature_c=28.0, light_umol_m2_s=120.0,
    )
    service.advance(START + timedelta(hours=5))
    service.stop()
    restored = DeviceService(DeviceSettings(), state_dir=tmp_path)
    assert restored.restored
    telemetry = restored.core.telemetry("reactor-2")
    assert telemetry["active_command_id"] == "c1"
    assert telemetry["hold_elapsed_h"] == pytest.approx(5.0, abs=0.01)


def test_paced_device_refuses_to_be_pushed():
    service = DeviceService(DeviceSettings(), speed=100.0, start_time=START)
    with pytest.raises(DeviceError) as err:
        service.advance(START + timedelta(hours=1))
    assert err.value.code == "device_free_running"


# -- network adapter against a real device service ------------------------------------


def test_discovered_and_drives_device_over_http():
    with device() as dev:
        clock = SteppedClock(START)
        ctrl = CultureControllerRegistry().create(
            "algae-sim-device", controller_id="c", settings={"url": dev.url}, clock=clock
        )
        assert isinstance(ctrl, CultureEnvironmentController)
        assert isinstance(ctrl, IntegratedCultureSensor)
        sensor = ctrl.culture_sensor()

        async def go():
            cmd = command()
            ack = await ctrl.apply_setpoints(cmd)
            await clock.sleep_until(START + timedelta(hours=12))  # AutoLab jumps ahead...
            telemetry = await ctrl.read_telemetry("reactor-1")  # ...the device follows
            await sensor.open()
            await sensor.self_test()
            reading = await sensor.read(od_request(cmd.experiment_id, uuid4(), "reactor-1"))
            safe = await ctrl.enter_safe_state("reactor-1", "hold complete")
            after = await ctrl.read_telemetry("reactor-1")
            return cmd, ack, telemetry, reading, safe, after

        cmd, ack, telemetry, reading, safe, after = run(go())
    assert ack.accepted and ack.applied_temperature_setpoint == q(30.0, "degree_Celsius")
    assert telemetry.sampled_at == START + timedelta(hours=12)
    assert telemetry.active_command_id == cmd.command_id
    assert abs(float(telemetry.measured_temperature.value) - 30.0) < 1.0
    assert reading.measured_at == START + timedelta(hours=12)
    assert reading.source_evidence.is_mock and float(reading.raw_value) > 0.1
    assert safe.reached and after.active_command_id is None


def test_autolab_clock_catches_up_with_a_device_that_is_ahead():
    with device() as dev:
        service = dev.service
        service.advance(START + timedelta(hours=3))  # device has been running longer
        clock = SteppedClock(START)
        ctrl = create_remote_controller(controller_id="c", settings={"url": dev.url}, clock=clock)
        run(ctrl.apply_setpoints(command()))
        assert clock.now() >= START + timedelta(hours=3)


def test_waits_for_a_paced_device_instead_of_pushing_it():
    with device(speed=36_000.0) as dev:  # 10 simulated hours per real second
        clock = SteppedClock(START)
        ctrl = create_remote_controller(controller_id="c", settings={"url": dev.url}, clock=clock)

        async def go():
            await ctrl.apply_setpoints(command())
            target = clock.now() + timedelta(hours=2)
            await clock.sleep_until(target)
            began = time.monotonic()
            telemetry = await ctrl.read_telemetry("reactor-1")
            return target, telemetry, time.monotonic() - began

        target, telemetry, waited = run(go())
    assert telemetry.sampled_at >= target
    assert 0.1 < waited < 5  # roughly 2 h / 10 h per second


def test_unreachable_device_is_retry_safe_not_ambiguous():
    ctrl = RemoteReactorController(
        controller_id="c",
        settings=RemoteSettings(url="http://127.0.0.1:9", request_timeout_s=0.5),
        clock=SteppedClock(START),
    )
    with pytest.raises(ControllerError) as err:
        run(ctrl.apply_setpoints(command()))
    assert err.value.code == "device_unreachable"
    assert err.value.retry_safe and not err.value.ambiguous
    with pytest.raises(SensorConnectionError):
        run(ctrl.sample_od("reactor-1"))


class FlakyClient:
    """Loses the first N replies to setpoint commands, after the device has acted."""

    def __init__(self, core: DeviceCore, lost_replies: int) -> None:
        self.core, self.lost, self.sends = core, lost_replies, []

    def request(self, method, path, body=None):
        if path == "/v1/clock":
            return {"device_time": self.core.now.isoformat(), "mode": "lockstep"}
        if path == "/v1/clock/advance":
            moved = self.core.advance_to(datetime.fromisoformat(body["to"]))
            return {"device_time": moved.isoformat()}
        if path.endswith("/setpoints"):
            self.sends.append(body)
            ack = self.core.apply_setpoints(reactor_id=path.split("/")[3], **body)
            if self.lost > 0:
                self.lost -= 1
                raise NoReply("reply lost")
            return ack
        raise AssertionError(path)


def flaky_controller(lost):
    core = DeviceCore(DeviceSettings(), start_time=START)
    client = FlakyClient(core, lost)
    ctrl = RemoteReactorController(
        controller_id="c", settings=RemoteSettings(resend_attempts=2),
        clock=SteppedClock(START), client=client,
    )
    return core, client, ctrl


def test_lost_reply_is_resolved_by_resending_the_same_command():
    core, client, ctrl = flaky_controller(lost=1)
    ack = run(ctrl.apply_setpoints(command()))
    assert ack.accepted
    assert len(client.sends) == 2 and client.sends[0] == client.sends[1]
    assert core.holds["reactor-1"] == 1  # the resend did not start a second batch


def test_persistent_silence_is_reported_as_ambiguous():
    core, _client, ctrl = flaky_controller(lost=10)
    with pytest.raises(ControllerError) as err:
        run(ctrl.apply_setpoints(command()))
    assert err.value.code == "setpoint_outcome_unknown" and err.value.ambiguous
    assert core.holds["reactor-1"] == 1  # it did act; AutoLab must not assume it didn't


def test_unknown_reactor_is_refused_by_the_device():
    with device() as dev:
        ctrl = create_remote_controller(
            controller_id="c", settings={"url": dev.url}, clock=SteppedClock(START)
        )
        with pytest.raises(ControllerError) as err:
            run(ctrl.read_telemetry("reactor-9"))
    assert err.value.code == "unknown_reactor"


def test_unreachable_is_distinguished_from_no_reply():
    from algae_sim.autolab.remote import DeviceClient

    with pytest.raises(Unreachable):
        DeviceClient("http://127.0.0.1:9", 0.5).request("GET", "/v1/clock")


# -- AutoLab crashes mid-culture; the device keeps the hold; AutoLab resumes ----------


def test_autolab_crash_and_resume_against_device(tmp_path):
    autolab = Path(sys.executable).parent / "autolab"
    with device(state_dir=tmp_path / "device") as dev:
        config = yaml.safe_load((ROOT / "configs/culture-device.yaml").read_text())
        config["campaign"]["max_experiments"] = 1
        config["optimization"]["startup_experiments"] = 1
        config["execution"]["controller"]["settings"]["url"] = dev.url
        (tmp_path / "campaign.yaml").write_text(yaml.safe_dump(config))
        db = ["--database", str(tmp_path / "db.sqlite3")]
        common = [*db, "--policy", str(ROOT / "configs/culture-policy.yaml"),
                  "--optimizer-root", str(tmp_path / "opt")]

        def cli(*args, **kw):
            return subprocess.run([str(autolab), *args], cwd=tmp_path, text=True,
                                  capture_output=True, check=True, **kw).stdout

        cli("init", *db)
        campaign = cli("culture", "create", "campaign.yaml", *common, "--id-only").strip()
        first = subprocess.Popen(
            [str(autolab), "culture", "run", campaign, *common],
            cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # Lockstep: AutoLab drives device time. Crash it part-way through the hold.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = dev.service.handle("GET", "/v1/status", {})["reactors"]["reactor-1"]
            if status["state"] == "holding" and status["hold_elapsed_h"] >= 6:
                break
            time.sleep(0.005)
        os.kill(first.pid, signal.SIGKILL)
        first.wait()
        held = dev.service.handle("GET", "/v1/status", {})["reactors"]["reactor-1"]
        assert held["state"] == "holding" and held["hold_elapsed_h"] < 36

        status = cli("culture", "run", campaign, *common)

    assert "state: completed" in status and "observed: 1" in status
    events = [
        json.loads(row)["code"] for row in subprocess.run(
            ["sqlite3", str(tmp_path / "db.sqlite3"),
             "select response_snapshot from action_events where event_type='event'"],
            text=True, capture_output=True, check=True,
        ).stdout.splitlines()
    ]
    assert "resumed" in events and "hold_not_confirmed" not in events
