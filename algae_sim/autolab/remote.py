"""Network AutoLab culture controller (adapter ``algae-sim-device``).

Talks HTTP/JSON to a running device service (``python -m algae_sim device serve``).
The reactors live in that separate process, so they keep holding temperature, and
cultures keep growing, while AutoLab is stopped. After a restart, AutoLab asks each
reactor which command it holds (``active_command_id``) and resumes the hold.

**Failure handling.** A network makes "did the command arrive?" a real question:

* *Unreachable* (connection refused): nothing was sent. Reported as retry-safe.
* *No reply* (timeout or dropped connection after sending): the device may have
  acted. The adapter resends the **same** ``command_id``; the device recognises the
  repeat and returns its original acknowledgement, so the command is never applied
  twice. If the device still cannot be heard, the adapter reports an **ambiguous**
  ``ControllerError``; AutoLab never retries those and goes to safe state.
* *Refused* (an HTTP error with a device error code) is passed through as-is.

**Time.** The device and AutoLab must agree on what time it is. Before each call the
adapter compares the device clock with AutoLab's clock:

* AutoLab behind: it waits on AutoLab's clock until the device's time (with
  ``execution.clock: simulated`` this is an instant jump);
* device behind: in lockstep mode it advances the device; in paced mode it waits for
  the device to get there in real time.

Use ``execution.clock: simulated`` with this adapter; a paced device runs faster
than a wall clock.

Settings (``execution.controller.settings``): ``url`` (default
``http://127.0.0.1:8750``), ``request_timeout_s`` (5), ``resend_attempts`` (2),
``clock_wait_timeout_s`` (600) and ``poll_interval_s`` (0.05).
"""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import threading
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from autonomous_lab.culture.controller import (
    ControllerError,
    EnvironmentTelemetry,
    SafeStateAcknowledgement,
    SetpointAcknowledgement,
    SetpointCommand,
)
from autonomous_lab.culture.models import CultureControllerCapabilities
from autonomous_lab.hardware.sensors.errors import SensorConnectionError

from algae_sim.autolab import _convert as convert
from algae_sim.autolab.sensor import OdSample, ReactorODSensor

ADAPTER_NAME = "algae-sim-device"


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep_until(self, when: datetime) -> None: ...


class Unreachable(Exception):
    """The request never reached the device."""


class NoReply(Exception):
    """The request may have reached the device, but no reply came back."""


class Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RemoteSettings:
    url: str = "http://127.0.0.1:8750"
    request_timeout_s: float = 5.0
    resend_attempts: int = 2
    clock_wait_timeout_s: float = 600.0
    poll_interval_s: float = 0.05

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> RemoteSettings:
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ControllerError(
                "controller_settings_invalid", f"unknown settings: {sorted(unknown)}"
            )
        return cls(**raw)  # type: ignore[arg-type]


class DeviceClient:
    """Small blocking HTTP/JSON client over one keep-alive connection.

    Classifies failures as ``Unreachable`` (never reached the device), ``NoReply``
    (may have reached it) or ``Refused`` (the device said no). Every device call is
    idempotent (reads, monotonic clock moves, deduplicated commands, safe state), so
    a request that fails on a *reused* connection, which the server may have closed
    while idle, is retried once on a fresh connection.
    """

    def __init__(self, base_url: str, timeout_s: float) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError(f"device url must be http://host:port, got {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.timeout_s = timeout_s
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        with self._lock:
            reused = self._conn is not None
            try:
                return self._send(method, path, body)
            except NoReply:
                if not reused:
                    raise
                self._close()
                return self._send(method, path, body)

    def _send(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        if self._conn is None:
            self._conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
            try:
                self._conn.connect()
            except (ConnectionRefusedError, socket.gaierror, OSError) as exc:
                self._close()
                raise Unreachable(str(exc)) from exc
        data = None if body is None else json.dumps(body).encode()
        try:
            headers = {"Content-Type": "application/json"}
            self._conn.request(method, path, body=data, headers=headers)
            response = self._conn.getresponse()
            payload = response.read()
        except (TimeoutError, ConnectionError, http.client.HTTPException, OSError) as exc:
            self._close()
            raise NoReply(str(exc) or type(exc).__name__) from exc
        try:
            decoded = json.loads(payload)
        except ValueError as exc:
            raise Refused("device_bad_response", f"HTTP {response.status}") from exc
        if response.status >= 400:
            raise Refused(decoded.get("error", "device_http_error"), decoded.get("message", ""))
        return decoded

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None


class RemoteReactorController:
    def __init__(
        self,
        *,
        controller_id: str,
        settings: RemoteSettings,
        clock: Clock | None,
        client: DeviceClient | None = None,
    ) -> None:
        if not controller_id:
            raise ValueError("controller_id must not be empty")
        self._controller_id = controller_id
        self._settings = settings
        self._clock = clock
        self._client = client or DeviceClient(settings.url, settings.request_timeout_s)
        self._manifest: dict[str, Any] | None = None
        self._sensor: ReactorODSensor | None = None
        self._known_device_time: datetime | None = None
        self._device_mode: str | None = None

    @property
    def controller_id(self) -> str:
        return self._controller_id

    # -- plumbing ----------------------------------------------------------------

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._client.request, method, path, body)

    def _manifest_now(self) -> dict[str, Any]:
        if self._manifest is None:
            try:
                self._manifest = self._client.request("GET", "/v1/manifest")
            except (Unreachable, NoReply) as exc:
                raise ControllerError(
                    "device_unreachable",
                    f"cannot reach device at {self._settings.url}: {exc}",
                    retry_safe=True,
                ) from exc
        return self._manifest

    async def _sync(self) -> datetime:
        """Make AutoLab's clock and the device clock agree; return the agreed time."""
        known = self._known_device_time
        if self._clock is not None and known is not None:
            ours = self._clock.now()
            if ours <= known:
                return known  # the device is already there; no need to ask
            if self._device_mode == "lockstep":
                reply = await self._call("POST", "/v1/clock/advance", {"to": ours.isoformat()})
                self._known_device_time = convert.ts(reply["device_time"])
                return self._known_device_time
        clock = await self._call("GET", "/v1/clock")
        self._device_mode = clock["mode"]
        device_now = convert.ts(clock["device_time"])
        self._known_device_time = device_now
        if self._clock is None:
            return device_now
        ours = self._clock.now()
        if device_now > ours:
            if not hasattr(self._clock, "subscribe") and (device_now - ours).total_seconds() > 60:
                raise ControllerError(
                    "clock_mismatch",
                    "device time is ahead of AutoLab's wall clock; "
                    "use execution.clock: simulated with a simulated device",
                )
            await self._clock.sleep_until(device_now)
            return device_now
        if ours > device_now:
            if clock["mode"] == "lockstep":
                await self._call("POST", "/v1/clock/advance", {"to": ours.isoformat()})
                self._known_device_time = ours
                return ours
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._settings.clock_wait_timeout_s
            while device_now < ours:
                if loop.time() > deadline:
                    raise ControllerError(
                        "device_clock_behind",
                        f"device did not reach {ours.isoformat()} in time",
                        retry_safe=True,
                    )
                await asyncio.sleep(self._settings.poll_interval_s)
                device_now = convert.ts((await self._call("GET", "/v1/clock"))["device_time"])
            self._known_device_time = device_now
        return ours

    async def _guarded(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """A read or idempotent call: unreachable/no-reply become retry-safe errors."""
        try:
            await self._sync()
            return await self._call(method, path, body)
        except (Unreachable, NoReply) as exc:
            raise ControllerError("device_unreachable", str(exc), retry_safe=True) from exc
        except Refused as exc:
            raise ControllerError(exc.code, str(exc)) from exc

    # -- AutoLab contract ------------------------------------------------------------

    async def capability_manifest(self) -> CultureControllerCapabilities:
        return convert.capabilities(self._controller_id, ADAPTER_NAME, self._manifest_now())

    async def apply_setpoints(self, command: SetpointCommand) -> SetpointAcknowledgement:
        try:
            fields = convert.command_fields(command, self._manifest_now())
        except ValueError as exc:
            return SetpointAcknowledgement(
                command_id=command.command_id,
                controller_id=self._controller_id,
                reactor_id=command.reactor_id,
                accepted=False,
                rejection_code="incompatible_units",
                rejection_message=str(exc),
                acknowledged_at=command.issued_at,
            )
        try:
            await self._sync()
        except (Unreachable, NoReply) as exc:
            raise ControllerError("device_unreachable", str(exc), retry_safe=True) from exc
        path = f"/v1/reactors/{command.reactor_id}/setpoints"
        body = {k: v for k, v in fields.items() if k != "reactor_id"}
        last: Exception | None = None
        for attempt in range(1 + self._settings.resend_attempts):
            try:
                ack = await self._call("POST", path, body)
                break
            except Unreachable as exc:
                if attempt == 0:
                    # Never reached the device: nothing happened, safe to report.
                    raise ControllerError(
                        "device_unreachable", str(exc), retry_safe=True
                    ) from exc
                last = exc  # an earlier send may have landed; keep resending
            except NoReply as exc:
                last = exc  # resend the same command_id; the device deduplicates
            except Refused as exc:
                raise ControllerError(exc.code, str(exc)) from exc
        else:
            raise ControllerError(
                "setpoint_outcome_unknown",
                f"no reply from device after {1 + self._settings.resend_attempts} sends: {last}",
                ambiguous=True,
            )
        acknowledgement = convert.acknowledgement(self._controller_id, ack, self._manifest_now())
        if self._clock is not None:
            await self._clock.sleep_until(acknowledgement.acknowledged_at)
        return acknowledgement

    async def read_telemetry(self, reactor_id: str) -> EnvironmentTelemetry:
        sample = await self._guarded("GET", f"/v1/reactors/{reactor_id}/telemetry")
        return convert.telemetry(self._controller_id, sample, self._manifest_now())

    async def enter_safe_state(self, reactor_id: str, reason: str) -> SafeStateAcknowledgement:
        try:
            await self._sync()
            ack = await self._call(
                "POST", f"/v1/reactors/{reactor_id}/safe-state", {"reason": reason}
            )
        except (Unreachable, NoReply) as exc:
            # We cannot confirm the reactor is safe: never pretend it is.
            raise ControllerError("safe_state_unconfirmed", str(exc), ambiguous=True) from exc
        except Refused as exc:
            raise ControllerError(exc.code, str(exc), ambiguous=True) from exc
        return convert.safe_state(self._controller_id, ack)

    def culture_sensor(self) -> ReactorODSensor:
        """The device's integrated OD750 probe, read over the same connection."""
        if self._sensor is None:
            sensor_id = f"{self._controller_id}-od750"
            self._sensor = ReactorODSensor(
                sensor_id=sensor_id,
                source=self,
                calibration=convert.calibration(sensor_id, self._manifest_now()),
                clock=(self._clock.now if self._clock is not None else _utc_now),
                transport=f"http:{self._settings.url}",
            )
        return self._sensor

    async def sample_od(self, reactor_id: str) -> OdSample | None:
        try:
            await self._sync()
            reading = await self._call("GET", f"/v1/reactors/{reactor_id}/od")
        except (Unreachable, NoReply) as exc:
            raise SensorConnectionError(f"device unreachable: {exc}") from exc
        except Refused as exc:
            if exc.code == "no_active_batch":
                return None
            raise SensorConnectionError(f"device refused OD read: {exc.code}") from exc
        return convert.od_sample(reading)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_remote_controller(
    *, controller_id: str, settings: Mapping[str, object], clock: object | None = None
) -> RemoteReactorController:
    """Entry-point factory for AutoLab's ``autonomous_lab.culture_controllers`` group."""
    if clock is not None and not callable(getattr(clock, "now", None)):
        raise ControllerError("controller_clock_invalid", "clock must provide now()")
    return RemoteReactorController(
        controller_id=controller_id,
        settings=RemoteSettings.from_mapping(settings),
        clock=clock,  # type: ignore[arg-type]
    )
