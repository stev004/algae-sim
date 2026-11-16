"""Device rules that stop clients and faults from doing unsafe things.

Each test is a scenario an external audit reproduced against an earlier version.
"""

from datetime import UTC, datetime, timedelta

import pytest

from algae_sim.device.core import MAX_HOLD_H, DeviceCore, DeviceError, DeviceSettings
from algae_sim.device.service import DeviceService

START = datetime(2026, 1, 1, tzinfo=UTC)
SETPOINTS = {"temperature": 30.0, "light_intensity": 150.0, "ph": 7.5}


def hold(core, command_id="c1", reactor="reactor-1", owner="client-a", **setpoints):
    return core.apply_setpoints(
        command_id=command_id, experiment_id=f"exp-{command_id}", reactor_id=reactor,
        setpoints={**SETPOINTS, **setpoints}, owner=owner,
    )


@pytest.fixture
def core():
    return DeviceCore(DeviceSettings(), start_time=START)


# -- idempotency ------------------------------------------------------------------


def test_reused_command_id_for_a_different_reactor_is_refused_not_called_a_duplicate(core):
    assert hold(core, "X", reactor="reactor-1")["accepted"]
    with pytest.raises(DeviceError) as err:
        hold(core, "X", reactor="reactor-2")
    assert err.value.code == "command_id_reused"
    assert "reactor-2" not in core.batches  # and nothing was started


def test_reused_command_id_with_different_setpoints_is_refused(core):
    hold(core, "X")
    with pytest.raises(DeviceError, match="different request"):
        hold(core, "X", temperature=32.0)


def test_an_exact_retry_is_still_a_duplicate(core):
    first = hold(core, "X")
    again = hold(core, "X")
    assert again == {**first, "duplicate": True} and core.holds["reactor-1"] == 1


# -- ownership --------------------------------------------------------------------


def test_another_client_cannot_replace_a_running_hold(core):
    hold(core, "a1", owner="client-a")
    refused = hold(core, "b1", owner="client-b", temperature=25.0)
    assert not refused["accepted"] and refused["rejection_code"] == "reactor_owned"
    assert core.batches["reactor-1"].command_id == "a1"


def test_the_owner_can_replace_its_own_hold(core):
    hold(core, "a1")
    assert hold(core, "a2", temperature=32.0)["accepted"]


def test_anyone_may_make_a_reactor_safe_but_only_the_owner_restarts_it(core):
    hold(core, "a1", owner="client-a")
    core.safe_state("reactor-1", "client-b saw something odd")
    assert core.batches["reactor-1"].reactor.safe_state
    assert hold(core, "b1", owner="client-b")["rejection_code"] == "reactor_owned"
    assert hold(core, "a2", owner="client-a")["accepted"]


def test_an_ownership_refusal_is_not_remembered(core):
    """Once the reactor is free, the very same command may succeed."""
    hold(core, "a1", owner="client-a")
    assert not hold(core, "b1", owner="client-b")["accepted"]
    core.release("reactor-1", "operator")
    assert hold(core, "b1", owner="client-b")["accepted"]


def test_owner_is_required(core):
    with pytest.raises(DeviceError, match="owner"):
        hold(core, owner="")


# -- latched protection -----------------------------------------------------------


def test_latched_over_temperature_trip_blocks_new_holds_until_released(core):
    hold(core, "a1")
    core.batches["reactor-1"].reactor.controller.tripped = True  # as if it had tripped
    refused = hold(core, "a2")
    assert refused["rejection_code"] == "protection_latched"
    ack = core.release("reactor-1", "inspected: probe reseated")
    assert ack["released"] and not core.batches["reactor-1"].reactor.controller.tripped
    assert hold(core, "a2")["accepted"]


def test_release_makes_the_reactor_safe_and_frees_it(core):
    hold(core, "a1", owner="client-a")
    core.release("reactor-1", "end of shift")
    assert core.batches["reactor-1"].reactor.safe_state
    assert core.status()["reactors"]["reactor-1"]["state"] == "released"


# -- watchdog ---------------------------------------------------------------------


def test_a_hold_ends_safely_at_the_maximum_hold_time(core):
    hold(core, "a1")
    core.advance_to(START + timedelta(hours=150))
    batch = core.batches["reactor-1"]
    assert batch.expired and batch.reactor.safe_state
    assert batch.reactor.elapsed_s <= MAX_HOLD_H * 3600
    telemetry = core.telemetry("reactor-1")
    assert "hold_expired" in telemetry["faults"] and telemetry["active_command_id"] is None
    # An expired hold no longer belongs to anyone.
    assert hold(core, "b1", owner="client-b")["accepted"]


def test_clock_moves_past_the_maximum_hold_cost_nothing_more(core):
    hold(core, "a1")
    core.advance_to(START + timedelta(hours=MAX_HOLD_H + 1))
    steps = core.batches["reactor-1"].reactor.elapsed_s
    core.advance_to(START + timedelta(days=365))
    assert core.batches["reactor-1"].reactor.elapsed_s == steps


def test_service_refuses_an_absurd_clock_advance():
    service = DeviceService(DeviceSettings(), start_time=START)
    with pytest.raises(DeviceError, match="at most"):
        service.advance(START + timedelta(days=365))


# -- service errors ---------------------------------------------------------------


def test_unexpected_errors_get_a_500_reply_not_a_dropped_connection(monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from algae_sim.device.service import make_handler

    service = DeviceService(DeviceSettings(), start_time=START)
    monkeypatch.setattr(service.core, "status", lambda: 1 / 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/status"
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(url, timeout=5)
        assert err.value.code == 500
        assert json.load(err.value)["error"] == "internal_error"
    finally:
        server.shutdown()
        server.server_close()


def test_cli_refuses_a_network_host_without_allow_remote():
    from algae_sim.cli import _require_loopback

    _require_loopback("127.0.0.1", False)
    _require_loopback("::1", False)
    _require_loopback("localhost", False)
    with pytest.raises(SystemExit, match="no authentication"):
        _require_loopback("0.0.0.0", False)
    _require_loopback("0.0.0.0", True)  # allowed, with a warning
