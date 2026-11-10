"""The local data pipeline: capture, parse/validate, store/dedupe, upload, analyse."""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from algae_sim.device.core import CAPTURE_INTERVAL_S, DeviceCore, DeviceSettings
from algae_sim.device.service import CAPTURE_FILE, DeviceService
from algae_sim.pipeline.analyse import hold_summaries, report
from algae_sim.pipeline.ingest import IngestService, serve
from algae_sim.pipeline.parse import EnvironmentRow, OdRow, Rejected, parse_line
from algae_sim.pipeline.uploader import IngestUnavailable, Uploader

START = datetime(2026, 11, 1, 8, tzinfo=UTC)


def capturing_core(**settings) -> DeviceCore:
    core = DeviceCore(DeviceSettings(**settings), start_time=START)
    core.enable_capture("bank")
    return core


def start_hold(core, reactor="reactor-1", setpoint=30.0, light=150.0, n=0):
    return core.apply_setpoints(
        command_id=f"cmd-{reactor}-{n}", experiment_id=f"exp-{n}", reactor_id=reactor,
        temperature_c=setpoint, light_umol_m2_s=light,
    )


def captured_lines(hours=2.0, **settings) -> list[str]:
    core = capturing_core(**settings)
    start_hold(core)
    core.advance_to(START + timedelta(hours=hours))
    core.read_od("reactor-1")
    return [json.dumps(m) for m in core.drain_outbox()]


# -- capture at the device -----------------------------------------------------------


def test_device_captures_a_sample_every_ten_minutes_and_every_od_reading():
    core = capturing_core()
    start_hold(core)
    core.advance_to(START + timedelta(hours=1))
    core.read_od("reactor-1")
    messages = core.drain_outbox()
    env = [m for m in messages if m["kind"] == "environment"]
    od = [m for m in messages if m["kind"] == "od"]
    assert [m["timestamp"] for m in env] == [
        (START + timedelta(seconds=i * CAPTURE_INTERVAL_S)).isoformat() for i in range(7)
    ]
    assert len(od) == 1
    assert [m["sequence"] for m in messages] == list(range(8))  # one counter per reactor
    assert {m["device_id"] for m in messages} == {"bank/reactor-1"}
    assert env[-1]["timestamp"] == (START + timedelta(hours=1)).isoformat()
    assert core.drain_outbox() == []


def test_capture_does_not_change_what_autolab_sees():
    def od_readings(capture: bool) -> list[float]:
        core = DeviceCore(DeviceSettings(), start_time=START)
        if capture:
            core.enable_capture("bank")
        start_hold(core)
        readings = []
        for hour in range(0, 37, 6):
            core.advance_to(START + timedelta(hours=hour))
            readings.append(core.read_od("reactor-1")["od750"])
        return readings

    assert od_readings(capture=True) == od_readings(capture=False)


def test_capture_is_off_by_default():
    core = DeviceCore(DeviceSettings(), start_time=START)
    start_hold(core)
    core.advance_to(START + timedelta(hours=1))
    assert core.outbox == []


def test_service_writes_the_capture_log_and_repairs_a_torn_line(tmp_path):
    service = DeviceService(DeviceSettings(), capture_dir=tmp_path, start_time=START)
    service.handle("POST", "/v1/reactors/reactor-1/setpoints", {
        "command_id": "c1", "experiment_id": "e1", "temperature_c": 30, "light_umol_m2_s": 150,
    })
    service.handle("POST", "/v1/clock/advance", {"to": (START + timedelta(hours=1)).isoformat()})
    log = tmp_path / CAPTURE_FILE
    lines = log.read_text().splitlines()
    assert len(lines) == 7 and all(parse_line(line) for line in lines)

    with log.open("a") as handle:
        handle.write('{"schema_version": "2", "kind": "envir')  # crash mid-write
    DeviceService(DeviceSettings(), capture_dir=tmp_path, start_time=START)
    assert log.read_text().endswith('"envir\n')  # torn line ended; next write starts clean


# -- parse and validate ----------------------------------------------------------------


def test_parser_accepts_device_messages_and_drops_simulator_truth():
    rows = [parse_line(line) for line in captured_lines()]
    assert isinstance(rows[0], EnvironmentRow) and isinstance(rows[-1], OdRow)
    assert rows[0].context.command_id == "cmd-reactor-1-0"
    assert not hasattr(rows[0], "simulator_truth")
    assert "truth" not in " ".join(EnvironmentRow.__slots__ + OdRow.__slots__)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda m: "not json at all", "invalid_json"),
        (lambda m: [m], "not_an_object"),
        (lambda m: {**m, "schema_version": "1"}, "unsupported_schema_version"),
        (lambda m: {**m, "sequence": True}, "invalid_sequence"),
        (lambda m: {**m, "sequence": -1}, "invalid_sequence"),
        (lambda m: {**m, "timestamp": "2026-11-01T08:00:00"}, "invalid_timestamp"),
        (lambda m: {k: v for k, v in m.items() if k != "device_id"}, "missing_field"),
        (lambda m: {**m, "measurements": {"temperature_c": 412.0}}, "implausible_value"),
        (lambda m: {**m, "measurements": {"temperature_c": "hot"}}, "invalid_field"),
        (lambda m: {**m, "energy_kwh": float("nan")}, "invalid_field"),
        (lambda m: {**m, "kind": "pressure"}, "unknown_kind"),
    ],
)
def test_parser_rejects_bad_messages_with_a_reason(change, reason):
    message = json.loads(captured_lines()[1])
    changed = change(message)
    line = changed if isinstance(changed, str) else json.dumps(changed)
    with pytest.raises(Rejected) as caught:
        parse_line(line)
    assert caught.value.reason == reason


def test_parser_normalises_timestamps_to_utc():
    message = json.loads(captured_lines()[0])
    message["timestamp"] = "2026-11-01T10:00:00+02:00"
    assert parse_line(json.dumps(message)).timestamp == "2026-11-01T08:00:00+00:00"


# -- ingest and store -------------------------------------------------------------------


def test_ingest_stores_once_keeps_raw_and_quarantines_bad_lines(tmp_path):
    service = IngestService(tmp_path)
    lines = captured_lines()
    body = ("\n".join(lines) + "\nnot json\n").encode()

    first = service.ingest(body)
    assert (first.lines, first.stored, first.duplicates) == (len(lines) + 1, len(lines), 0)
    assert first.rejected == [{"line": len(lines) + 1, "reason": "invalid_json"}]

    again = service.ingest(body)  # a resend after a lost reply
    assert (again.stored, again.duplicates) == (0, len(lines))

    raw = sorted((tmp_path / "raw").rglob("*.jsonl"))
    assert len(raw) == 2 and raw[0].read_bytes() == body  # kept exactly as received
    db = sqlite3.connect(tmp_path / "pipeline.sqlite3")
    assert db.execute("select count(*) from environment").fetchone()[0] == len(lines) - 1
    assert db.execute("select count(*) from od_readings").fetchone()[0] == 1
    assert db.execute("select count(*) from rejected").fetchone()[0] == 2


# -- uploader ---------------------------------------------------------------------------


class RunningIngest:
    def __init__(self, data_dir) -> None:
        self.service = IngestService(data_dir)
        self._ready = threading.Event()
        threading.Thread(
            target=serve, args=(self.service,),
            kwargs={"port": 0, "ready": self._on_ready}, daemon=True,
        ).start()
        self._ready.wait(5)

    def _on_ready(self, server) -> None:
        self.server = server
        self._ready.set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()


def write_log(path, lines, tail=""):
    path.write_text("".join(line + "\n" for line in lines) + tail)


def test_uploader_buffers_during_an_outage_then_drains(tmp_path):
    log = tmp_path / "capture" / CAPTURE_FILE
    log.parent.mkdir()
    lines = captured_lines(hours=6)
    write_log(log, lines, tail='{"partial": ')  # the device is mid-write

    uploader = Uploader(log, "http://127.0.0.1:9", batch_lines=10)  # nothing listens there
    with pytest.raises(IngestUnavailable):
        uploader.upload_once()
    assert uploader.cursor() == 0  # nothing acknowledged, nothing lost
    assert uploader.pending_lines() == len(lines)  # the torn tail isn't counted

    ingest = RunningIngest(tmp_path / "pipeline")
    try:
        uploader = Uploader(log, ingest.url, batch_lines=10)
        sent = uploader.drain()
        assert sum(s.stored for s in sent) == len(lines)
        assert len(sent) == -(-len(lines) // 10)
        assert uploader.pending_lines() == 0
        assert uploader.cursor() == log.stat().st_size - len('{"partial": ')

        uploader.reset()  # replay everything
        replay = uploader.drain()
        assert sum(s.duplicates for s in replay) == len(lines)
        assert sum(s.stored for s in replay) == 0
    finally:
        ingest.stop()


def test_uploader_follow_mode_waits_for_the_log_then_stops_when_idle(tmp_path):
    log = tmp_path / CAPTURE_FILE
    ingest = RunningIngest(tmp_path / "pipeline")
    messages: list[str] = []

    def sleep(seconds: float) -> None:
        if not log.exists():
            write_log(log, captured_lines(hours=1))

    try:
        Uploader(log, ingest.url).run(
            follow=True, poll_s=0.01, exit_when_idle_s=0.05, report=messages.append, sleep=sleep,
        )
    finally:
        ingest.stop()
    assert messages[0].startswith("waiting for")
    assert any(m.startswith("sent 8 lines: 8 stored") for m in messages)
    assert messages[-1] == "caught up and idle; stopping"


# -- analysis ---------------------------------------------------------------------------


def test_analysis_flags_the_replicate_whose_heater_failed(tmp_path):
    core = capturing_core(heater_faults={"reactor-2": (0,)}, heater_fault_after_h=4.0)
    for reactor in ("reactor-1", "reactor-2", "reactor-3"):
        start_hold(core, reactor=reactor)
    for hour in range(0, 37, 6):
        core.advance_to(START + timedelta(hours=hour))
        for reactor in ("reactor-1", "reactor-2", "reactor-3"):
            core.read_od(reactor)
    service = IngestService(tmp_path)
    service.ingest("".join(json.dumps(m) + "\n" for m in core.drain_outbox()).encode())

    summaries = {s.device_id: s for s in hold_summaries(service.db.connection)}
    assert summaries["bank/reactor-2"].flags == ["temperature_off_setpoint"]
    assert "heater_not_responding" in summaries["bank/reactor-2"].faults
    assert summaries["bank/reactor-1"].flags == []
    assert summaries["bank/reactor-1"].mean_abs_deviation_c < 0.5
    assert summaries["bank/reactor-1"].od_readings == 7
    assert summaries["bank/reactor-1"].growth_rate_per_h > 0
    assert summaries["bank/reactor-2"].energy_kwh < summaries["bank/reactor-1"].energy_kwh

    health = report(tmp_path)["pipeline"]
    assert health["devices"] == ["bank/reactor-1", "bank/reactor-2", "bank/reactor-3"]
    assert health["duplicates_dropped"] == 0
