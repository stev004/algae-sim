"""The device-side uploader: ship the capture log to the ingest endpoint, reliably.

    python -m algae_sim pipeline upload --capture runs/device/capture --follow

It runs as its own process beside the device (like an edge agent on a lab PC), so a
pipeline outage never touches the device or its control loop. The rules:

* **The capture log is the buffer.** Nothing is held only in memory; if the
  pipeline is down, lines simply wait in the log.
* **A cursor marks what has been acknowledged.** It's a byte offset into the log,
  saved atomically *after* the ingest replies 200. A crash before that point means
  the batch is sent again: at-least-once delivery. The ingest's
  ``(device_id, sequence)`` key turns the resend into counted duplicates.
* **Only complete lines are sent.** A line the device is still writing is left
  for the next pass.
* **Back off while the pipeline is unreachable**, doubling the wait up to a cap.
"""

from __future__ import annotations

import http.client
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

CURSOR_FILE = "uploader-cursor.json"
DEFAULT_BATCH_LINES = 500


class IngestUnavailable(Exception):
    """The batch wasn't confirmed stored (unreachable, timed out or 5xx). Retry later."""


class IngestRefused(Exception):
    """The ingest rejected the whole request (4xx). Resending the same bytes won't help."""


@dataclass(frozen=True, slots=True)
class Sent:
    lines: int
    stored: int
    duplicates: int
    rejected: int
    pending_after: int  # complete lines still waiting in the log


class Uploader:
    def __init__(
        self,
        capture_file: Path,
        url: str,
        *,
        cursor_file: Path | None = None,
        batch_lines: int = DEFAULT_BATCH_LINES,
        timeout_s: float = 10.0,
    ) -> None:
        self.capture_file = Path(capture_file)
        self.cursor_file = cursor_file or self.capture_file.parent / CURSOR_FILE
        self.batch_lines = batch_lines
        self.timeout_s = timeout_s
        parts = urlsplit(url)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 80
        self.path = (parts.path.rstrip("/") or "") + "/v1/telemetry"

    # -- cursor ------------------------------------------------------------------------

    def cursor(self) -> int:
        try:
            return int(json.loads(self.cursor_file.read_text())["offset"])
        except FileNotFoundError:
            return 0

    def _save_cursor(self, offset: int) -> None:
        tmp = self.cursor_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"offset": offset}) + "\n")
        os.replace(tmp, self.cursor_file)

    def reset(self) -> None:
        """Forget all acknowledgements, so the whole log is sent again (a replay)."""
        self._save_cursor(0)

    # -- reading -------------------------------------------------------------------------

    def _read_pending(self) -> tuple[bytes, int, int]:
        """Return (next batch of complete lines, its end offset, lines left after it)."""
        start = self.cursor()
        try:
            with self.capture_file.open("rb") as handle:
                handle.seek(start)
                data = handle.read()
        except FileNotFoundError:
            return b"", start, 0
        complete = data[: data.rfind(b"\n") + 1]  # drop a line still being written
        lines = complete.splitlines(keepends=True)
        batch = b"".join(lines[: self.batch_lines])
        return batch, start + len(batch), max(0, len(lines) - self.batch_lines)

    def pending_lines(self) -> int:
        batch, _, rest = self._read_pending()
        return batch.count(b"\n") + rest

    # -- sending -------------------------------------------------------------------------

    def upload_once(self) -> Sent | None:
        """Send one batch. Returns None if there was nothing to send."""
        batch, end, rest = self._read_pending()
        if not batch:
            return None
        reply = self._post(batch)
        self._save_cursor(end)  # only now is the batch acknowledged
        return Sent(
            lines=int(reply["lines"]),
            stored=int(reply["stored"]),
            duplicates=int(reply["duplicates"]),
            rejected=len(reply["rejected"]),
            pending_after=rest,
        )

    def _post(self, body: bytes) -> dict:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        try:
            connection.request(
                "POST", self.path, body=body,
                headers={"Content-Type": "application/x-ndjson"},
            )
            response = connection.getresponse()
            payload = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise IngestUnavailable(f"{type(exc).__name__}: {exc}") from exc
        finally:
            connection.close()
        if response.status >= 500:
            raise IngestUnavailable(f"HTTP {response.status}: {payload[:200]!r}")
        if response.status != 200:
            raise IngestRefused(f"HTTP {response.status}: {payload[:200]!r}")
        return json.loads(payload)

    def drain(self) -> list[Sent]:
        """Send batches until the log is caught up. Raises IngestUnavailable on outage."""
        sent = []
        while (result := self.upload_once()) is not None:
            sent.append(result)
        return sent

    def run(
        self,
        *,
        follow: bool = False,
        poll_s: float = 0.5,
        max_backoff_s: float = 8.0,
        exit_when_idle_s: float | None = None,
        report: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Upload until caught up; with ``follow``, keep watching the log for new lines."""
        backoff = poll_s
        idle_since = time.monotonic()
        announced_wait = False
        while True:
            if follow and not self.capture_file.exists():
                # The device hasn't captured anything yet: waiting, not idle.
                if not announced_wait:
                    report(f"waiting for {self.capture_file} to appear")
                    announced_wait = True
                idle_since = time.monotonic()
                sleep(poll_s)
                continue
            try:
                result = self.upload_once()
            except IngestUnavailable as exc:
                report(f"ingest unavailable ({exc}); {self.pending_lines()} lines buffered, "
                       f"retrying in {backoff:g} s")
                sleep(backoff)
                backoff = min(backoff * 2, max_backoff_s)
                idle_since = time.monotonic()
                continue
            backoff = poll_s
            if result is not None:
                report(f"sent {result.lines} lines: {result.stored} stored, "
                       f"{result.duplicates} duplicates, {result.rejected} rejected; "
                       f"{result.pending_after} waiting")
                idle_since = time.monotonic()
                continue
            if not follow:
                return
            if exit_when_idle_s is not None and time.monotonic() - idle_since >= exit_when_idle_s:
                report("caught up and idle; stopping")
                return
            sleep(poll_s)
