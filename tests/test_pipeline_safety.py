"""Pipeline cases where data could be lost or silently miscounted.

Each test is a scenario an external audit reproduced against an earlier version.
"""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from algae_sim.device.core import DeviceSettings
from algae_sim.device.service import CAPTURE_FILE, DeviceService
from algae_sim.pipeline.ingest import IngestService
from algae_sim.pipeline.uploader import IngestUnavailable, Uploader

START = datetime(2026, 1, 1, tzinfo=UTC)
SETPOINTS = {"temperature": 30, "light_intensity": 150, "ph": 7.5}


def device(tmp_path, state="state"):
    return DeviceService(
        DeviceSettings(), start_time=START, state_dir=tmp_path / state,
        capture_dir=tmp_path / "capture",
    )


def run_hold(service, hours, command_id="c1"):
    service.handle("POST", "/v1/reactors/reactor-1/setpoints", {
        "command_id": command_id, "experiment_id": "e1", "owner": "client-a",
        "setpoints": SETPOINTS,
    })
    service.handle(
        "POST", "/v1/clock/advance", {"to": (service.core.now + timedelta(hours=hours)).isoformat()}
    )


def sequences(tmp_path):
    lines = (tmp_path / "capture" / CAPTURE_FILE).read_text().splitlines()
    return [json.loads(line)["sequence"] for line in lines]


# -- the device never reuses a sequence number --------------------------------------


def test_a_lost_state_file_does_not_restart_sequence_numbers(tmp_path):
    first = device(tmp_path)
    run_hold(first, hours=1)
    first.stop()
    before = sequences(tmp_path)

    # The state file is gone (deleted to clear a firmware mismatch, a new disk...).
    fresh = device(tmp_path, state="new-state")
    run_hold(fresh, hours=1, command_id="c2")
    after = sequences(tmp_path)[len(before):]
    assert min(after) > max(before)  # earlier: the new lines reused 0.. and were lost


def test_a_rollback_to_older_state_does_not_reuse_sequence_numbers(tmp_path):
    service = device(tmp_path)
    run_hold(service, hours=1)
    service.persist()
    saved = (tmp_path / "state" / "device.pickle").read_bytes()
    service.handle("GET", "/v1/reactors/reactor-1/od", {})  # logged, never saved to state
    service.flush_capture()
    before = sequences(tmp_path)

    (tmp_path / "state" / "device.pickle").write_bytes(saved)  # crash: state is behind
    restored = device(tmp_path)
    restored.handle("GET", "/v1/reactors/reactor-1/od", {})
    restored.flush_capture()
    after = sequences(tmp_path)
    assert len(set(after)) == len(after) and after[-1] > max(before)


# -- the store tells a true repeat from a reused key --------------------------------


def test_a_reused_sequence_with_different_data_is_quarantined_not_dropped(tmp_path):
    service = device(tmp_path)
    run_hold(service, hours=0.5)
    service.stop()
    line = (tmp_path / "capture" / CAPTURE_FILE).read_text().splitlines()[0]
    message = json.loads(line)
    message["measurements"]["temperature_c"] = 12.3  # same key, different reading

    ingest = IngestService(tmp_path / "pipeline")
    ingest.ingest((line + "\n").encode())
    repeat = ingest.ingest((line + "\n").encode())
    conflict = ingest.ingest((json.dumps(message) + "\n").encode())
    assert repeat.duplicates == 1 and not repeat.rejected
    assert conflict.duplicates == 0 and conflict.stored == 0
    assert conflict.rejected == [{"line": 1, "reason": "sequence_conflict"}]
    db = sqlite3.connect(tmp_path / "pipeline" / "pipeline.sqlite3")
    (kept,) = db.execute("select raw from rejected where reason = 'sequence_conflict'").fetchone()
    assert json.loads(kept)["measurements"]["temperature_c"] == 12.3


# -- the uploader only moves its cursor on a real acknowledgement --------------------


def make_log(tmp_path, n=5):
    log = tmp_path / CAPTURE_FILE
    log.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(n)))
    return log


@pytest.mark.parametrize(
    "reply",
    [
        {"ok": True},  # 200 without the expected fields
        {"lines": "five", "stored": 5, "duplicates": 0, "rejected": []},
        {"lines": 2, "stored": 2, "duplicates": 0, "rejected": []},  # not our batch
    ],
)
def test_an_unreadable_reply_leaves_the_batch_pending(tmp_path, reply):
    log = make_log(tmp_path)
    uploader = Uploader(log, "http://unused", sender=lambda body: reply)
    with pytest.raises(IngestUnavailable):
        uploader.upload_once()
    assert uploader.cursor() == 0 and uploader.pending_lines() == 5


def ok(body):
    n = body.count(b"\n")
    return {"lines": n, "stored": n, "duplicates": 0, "rejected": []}


def test_a_replaced_log_is_sent_from_its_start(tmp_path):
    log = make_log(tmp_path, n=5)
    uploader = Uploader(log, "http://unused", sender=ok)
    uploader.drain()
    assert uploader.pending_lines() == 0

    log.unlink()  # rotated: a new, shorter file takes its place
    log.write_text(json.dumps({"n": "new"}) + "\n")
    assert uploader.pending_lines() == 1  # earlier: silently 0 pending
    assert uploader.drain()[0].lines == 1


def test_a_replaced_log_that_is_longer_is_also_detected(tmp_path):
    log = make_log(tmp_path, n=2)
    uploader = Uploader(log, "http://unused", sender=ok)
    uploader.drain()
    log.unlink()
    make_log(tmp_path, n=8)  # a bigger file: the old offset would skip its start
    assert uploader.pending_lines() == 8
