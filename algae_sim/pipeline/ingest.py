"""The ingest endpoint: receive a batch, keep it raw, parse it, store it, report back.

    python -m algae_sim pipeline ingest --data runs/pipeline

``POST /v1/telemetry`` takes newline-delimited JSON (one captured message per line)
and answers with what happened to every line::

    {"batch_id": ..., "lines": 120, "stored": 118, "duplicates": 1,
     "rejected": [{"line": 7, "reason": "invalid_json"}]}

A 200 reply means the batch is durably stored (raw file and database transaction
both done), so the uploader may advance its cursor. Rejected lines are part of a
successful reply: they're quarantined with a reason, and resending them would never
help. Only failures to *store* return an error, which tells the uploader to retry.

This is the stand-in for an Azure Function with an HTTP trigger: the handler logic
is ``IngestService.ingest``, independent of the web server around it. It listens on
127.0.0.1 with no authentication, so it's for one local machine.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from algae_sim.pipeline.parse import Rejected, Row, parse_line
from algae_sim.pipeline.store import BatchOutcome, Database, RawStore

MAX_BATCH_BYTES = 4 * 1024 * 1024


class IngestService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.raw = RawStore(self.data_dir / "raw")
        self.db = Database(self.data_dir / "pipeline.sqlite3")

    def ingest(self, body: bytes) -> BatchOutcome:
        received_at = datetime.now(UTC)
        batch_id = f"{received_at:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        raw_path = self.raw.put(batch_id, received_at, body)  # raw first: always auditable
        outcome = BatchOutcome(batch_id=batch_id)
        rows: list[Row] = []
        rejections: list[tuple[int, str, str, str]] = []
        for number, line in enumerate(body.splitlines(), start=1):
            if not line.strip():
                continue
            outcome.lines += 1
            try:
                rows.append(parse_line(line))
            except Rejected as exc:
                text = line.decode("utf-8", errors="replace")[:2000]
                rejections.append((number, exc.reason, exc.detail, text))
                outcome.rejected.append({"line": number, "reason": exc.reason})
        return self.db.record_batch(
            outcome, rows, rejections, received_at=received_at, raw_path=raw_path
        )

    def close(self) -> None:
        self.db.close()


def make_handler(service: IngestService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "algae-sim-ingest"
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def _send(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path == "/v1/health":
                self._send(HTTPStatus.OK, {"status": "ok"})
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if self.path != "/v1/telemetry":
                self.rfile.read(length)
                self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if length > MAX_BATCH_BYTES:
                self.close_connection = True
                self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "batch_too_large"})
                return
            body = self.rfile.read(length)
            try:
                outcome = service.ingest(body)
            except (OSError, sqlite3.Error) as exc:
                # Disk or database trouble: nothing is confirmed stored, so the uploader retries.
                self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "store_failed",
                                                            "message": str(exc)})
                return
            self._send(HTTPStatus.OK, outcome.as_dict())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def serve(
    service: IngestService,
    *,
    host: str = "127.0.0.1",
    port: int = 8760,
    ready: Callable[[ThreadingHTTPServer], None] | None = None,
) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(service))
    if ready is not None:
        ready(server)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        service.close()
