"""The reactor bank as a long-running local device service (HTTP/JSON, stdlib only).

    python -m algae_sim device serve --speed 3600 --state runs/device

The service hosts one ``DeviceCore`` and keeps it running independently of any
client, the way a real bioreactor controller keeps holding temperature when the lab
computer reboots. It persists its state to ``<state>/device.pickle`` after every
command and periodically, so the service itself can also be stopped and restarted.

Time modes:

* **lockstep** (``--speed 0``): device time only moves when a client calls
  ``POST /v1/clock/advance``. Fast and deterministic; used by tests and CI.
* **paced** (``--speed N``): device time runs on its own at N x real time
  (3600 means one simulated hour per real second). Clients cannot push it forward;
  they wait for it. While no client is connected, cultures keep growing.

Capture (``--capture DIR``): every environment sample and OD reading the firmware
records is appended to ``DIR/telemetry.jsonl``, one JSON message per line, before the
state file is saved. A crash between the two can repeat messages on restart but never
lose one (at-least-once); the pipeline removes repeats by device ID and sequence.

Listens on 127.0.0.1 only by default. There is no authentication, so it is meant
for a single local machine.

API (JSON bodies; errors are ``{"error": code, "message": text}``):

    GET  /v1/status                      device time, mode, every reactor
    GET  /v1/manifest                    capabilities and calibration
    GET  /v1/clock                       device time and mode
    POST /v1/clock/advance   {to}        lockstep only
    POST /v1/reactors/<id>/setpoints  {command_id, experiment_id, temperature_c, light_umol_m2_s}
    GET  /v1/reactors/<id>/telemetry
    POST /v1/reactors/<id>/safe-state {reason}
    GET  /v1/reactors/<id>/od
"""

from __future__ import annotations

import json
import os
import pickle
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from algae_sim.device.core import DeviceCore, DeviceError, DeviceSettings

STATE_FILE = "device.pickle"
CAPTURE_FILE = "telemetry.jsonl"
TICK_S = 0.05
PERSIST_EVERY_S = 1.0

ERROR_STATUS = {
    "unknown_reactor": HTTPStatus.NOT_FOUND,
    "not_found": HTTPStatus.NOT_FOUND,
    "bad_request": HTTPStatus.BAD_REQUEST,
}


class DeviceService:
    """Thread-safe owner of the device: clock, persistence and command dispatch."""

    def __init__(
        self,
        settings: DeviceSettings,
        *,
        speed: float = 0.0,
        state_dir: Path | None = None,
        device_id: str = "algae-sim-device",
        start_time: datetime | None = None,
        capture_dir: Path | None = None,
    ) -> None:
        if speed < 0:
            raise ValueError("speed must be >= 0")
        self.speed = speed
        self.device_id = device_id
        self.state_path = None if state_dir is None else Path(state_dir) / STATE_FILE
        self.lock = threading.RLock()
        self.restored = False
        if self.state_path is not None and self.state_path.exists():
            # Our own state file, written by this service; never load untrusted pickles.
            with self.state_path.open("rb") as handle:
                self.core: DeviceCore = pickle.load(handle)
            self.restored = True
        else:
            self.core = DeviceCore(settings, start_time=start_time or datetime.now(UTC))
        self.capture_path = None if capture_dir is None else Path(capture_dir) / CAPTURE_FILE
        if self.capture_path is not None:
            self.core.enable_capture(device_id)
            self._repair_capture_log()
        self._anchor_device = self.core.now
        self._anchor_mono = time.monotonic()
        self._last_persist = 0.0
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None

    @property
    def mode(self) -> str:
        return "paced" if self.speed > 0 else "lockstep"

    # -- clock -----------------------------------------------------------------------

    def start(self) -> None:
        if self.speed > 0 and self._ticker is None:
            self._ticker = threading.Thread(
                target=self._tick_loop, name="device-clock", daemon=True
            )
            self._ticker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ticker is not None:
            self._ticker.join(timeout=2)
        with self.lock:
            self.persist()

    def _tick_loop(self) -> None:
        while not self._stop.wait(TICK_S):
            with self.lock:
                self._tick()
                if time.monotonic() - self._last_persist >= PERSIST_EVERY_S:
                    self.persist()
                else:
                    self.flush_capture()

    def _tick(self) -> None:
        if self.speed > 0:
            elapsed = (time.monotonic() - self._anchor_mono) * self.speed
            self.core.advance_to(self._anchor_device + timedelta(seconds=elapsed))

    def advance(self, to: datetime) -> datetime:
        if self.speed > 0:
            raise DeviceError(
                "device_free_running",
                "device time runs on its own in paced mode; wait for it instead",
            )
        now = self.core.advance_to(to)
        # Clock moves are frequent; save at most once a second (commands save at once).
        if time.monotonic() - self._last_persist >= PERSIST_EVERY_S:
            self.persist()
        return now

    def _repair_capture_log(self) -> None:
        """End a line a crash left half-written, so the next message starts cleanly.

        The torn line stays in the log; the pipeline's parser rejects and quarantines
        it, which is the honest record of what happened.
        """
        path = self.capture_path
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 0:
            with path.open("rb+") as handle:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")

    def flush_capture(self) -> int:
        """Append the firmware's outbox to the capture log and fsync it. Returns lines."""
        messages = self.core.drain_outbox()
        if not messages or self.capture_path is None:
            return 0
        data = "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in messages)
        with self.capture_path.open("a") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return len(messages)

    def persist(self) -> None:
        # Log first, state second: a crash in between replays messages, never loses them.
        self.flush_capture()
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        with tmp.open("wb") as handle:
            pickle.dump(self.core, handle)
        os.replace(tmp, self.state_path)  # atomic: a crash never leaves half a file
        self._last_persist = time.monotonic()

    # -- request handling ------------------------------------------------------------

    def handle(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            try:
                return self._route(method, path, body)
            finally:
                self.flush_capture()

    def _route(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self._tick()
        parts = [p for p in path.split("/") if p]
        if parts[:1] != ["v1"]:
            raise DeviceError("not_found", f"no route {path}")
        route = parts[1:]
        if method == "GET" and route == ["status"]:
            return self._envelope(self.core.status())
        if method == "GET" and route == ["manifest"]:
            return {**self.core.manifest(), "device_id": self.device_id}
        if method == "GET" and route == ["clock"]:
            return self._envelope({})
        if method == "POST" and route == ["clock", "advance"]:
            self.advance(_time(body, "to"))
            return self._envelope({})
        if len(route) == 3 and route[0] == "reactors":
            return self._reactor(method, route[1], route[2], body)
        raise DeviceError("not_found", f"no route {method} {path}")

    def _reactor(
        self, method: str, reactor_id: str, action: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if method == "POST" and action == "setpoints":
            ack = self.core.apply_setpoints(
                command_id=_text(body, "command_id"),
                experiment_id=_text(body, "experiment_id"),
                reactor_id=reactor_id,
                temperature_c=_number(body, "temperature_c"),
                light_umol_m2_s=_number(body, "light_umol_m2_s"),
            )
            self.persist()
            return ack
        if method == "GET" and action == "telemetry":
            return self.core.telemetry(reactor_id)
        if method == "POST" and action == "safe-state":
            ack = self.core.safe_state(reactor_id, str(body.get("reason", "requested")))
            self.persist()
            return ack
        if method == "GET" and action == "od":
            reading = self.core.read_od(reactor_id)
            if reading is None:
                raise DeviceError("no_active_batch", f"reactor {reactor_id!r} has no culture")
            return reading
        raise DeviceError("not_found", f"no route {method} {action}")

    def _envelope(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "mode": self.mode,
            "speed": self.speed,
            **extra,
            "device_time": self.core.now.isoformat(),
        }


def _time(body: dict[str, Any], key: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(body[key]))
    except (KeyError, ValueError) as exc:
        raise DeviceError("bad_request", f"{key} must be an ISO-8601 timestamp") from exc
    if value.tzinfo is None:
        raise DeviceError("bad_request", f"{key} must include a timezone")
    return value


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise DeviceError("bad_request", f"{key} is required")
    return value


def _number(body: dict[str, Any], key: str) -> float:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DeviceError("bad_request", f"{key} must be a number")
    return float(value)


def make_handler(service: DeviceService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "algae-sim-device"
        protocol_version = "HTTP/1.1"  # keep-alive: one connection per client
        disable_nagle_algorithm = True  # small JSON replies go out immediately

        def _respond(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                if not isinstance(body, dict):
                    raise DeviceError("bad_request", "body must be a JSON object")
                payload, status = service.handle(method, self.path, body), HTTPStatus.OK
            except json.JSONDecodeError:
                payload = {"error": "bad_request", "message": "invalid JSON"}
                status = HTTPStatus.BAD_REQUEST
            except DeviceError as exc:
                payload = {"error": exc.code, "message": str(exc)}
                status = ERROR_STATUS.get(exc.code, HTTPStatus.CONFLICT)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            self._respond("GET")

        def do_POST(self) -> None:
            self._respond("POST")

        def log_message(self, format: str, *args: Any) -> None:
            pass  # quiet; the service reports its own lifecycle

    return Handler


def serve(
    service: DeviceService,
    *,
    host: str = "127.0.0.1",
    port: int = 8750,
    ready: Callable[[ThreadingHTTPServer], None] | None = None,
) -> None:
    """Run until interrupted. ``ready`` is called once the socket is listening."""
    server = ThreadingHTTPServer((host, port), make_handler(service))
    service.start()
    if ready is not None:
        ready(server)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        service.stop()
