"""The Azure sink and schema, tested with fakes: no Azure account is touched."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from algae_sim.azure.records import (
    TABLES,
    batch_id,
    blob_name,
    mapping_name,
    record,
    schema_kql,
    split_batch,
)
from algae_sim.pipeline.parse import parse_line
from algae_sim.pipeline.uploader import IngestUnavailable, Uploader
from tests.test_pipeline import captured_lines

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-11-15T12:00:00+00:00"


def batch(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode()


class TestRecords:
    def test_records_have_exactly_the_table_columns(self):
        lines = captured_lines(hours=1.0)
        kinds = {json.loads(line)["kind"] for line in lines}
        assert kinds == {"environment", "od"}
        for line in lines:
            table, rec = record(parse_line(line), "b", NOW)
            assert list(rec) == [name for name, _ in TABLES[table]]

    def test_a_batch_splits_into_tables_and_keeps_every_rejection(self):
        lines = captured_lines(hours=1.0)
        body = batch([*lines, "{not json", json.dumps({"schema_version": "1"})])
        tables, rejected = split_batch(body, NOW)
        assert len(tables["Environment"]) + len(tables["Od"]) == len(lines)
        assert [r["reason"] for r in rejected] == ["invalid_json", "unsupported_schema_version"]
        assert [r["reason"] for r in tables["Rejected"]] == [r["reason"] for r in rejected]
        assert "simulator_truth" not in json.dumps(tables)  # never leaves the parser

    def test_batch_ids_are_content_addressed(self):
        a, b = batch(["x"]), batch(["y"])
        assert batch_id(a) == batch_id(batch(["x"])) != batch_id(b)
        assert blob_name(a).endswith(".ndjson")


class TestSchema:
    def test_committed_schema_matches_the_generator(self):
        committed = (ROOT / "algae_sim/azure/schema.kql").read_text()
        assert committed == schema_kql(), "run: python -m algae_sim.azure schema > ..."

    def test_schema_creates_every_table_mapping_and_dedupe_view(self):
        kql = schema_kql()
        for table in TABLES:
            assert f".create-merge table {table} (" in kql
            assert f"ingestion json mapping '{mapping_name(table)}'" in kql
        assert "EnvironmentClean on table Environment" in kql
        assert "summarize arg_max(ingested_at, *) by device_id, sequence" in kql
        assert "policy ingestionbatching" in kql and "00:00:30" in kql


class FakeContainer:
    def __init__(self, fail=False):
        self.blobs, self.fail = {}, fail

    def upload_blob(self, name, data, overwrite):
        if self.fail:
            raise ConnectionError("network down")
        assert overwrite
        self.blobs[name] = data


class FakeIngest:
    def __init__(self):
        self.calls = []

    def ingest_from_stream(self, stream, ingestion_properties):
        rows = [json.loads(x) for x in stream.read().splitlines()]
        props = ingestion_properties
        self.calls.append((props.table, props.ingestion_mapping_reference, rows))


@pytest.fixture
def sink_parts():
    pytest.importorskip("azure.kusto.ingest")
    from algae_sim.azure.sink import AzureConfig, AzureSink

    config = AzureConfig(tenant_id="t", storage_account="s", cluster_uri="https://c")
    container, ingest = FakeContainer(), FakeIngest()
    sink = AzureSink(config, container=container, ingest=ingest,
                     clock=lambda: datetime(2026, 11, 15, 12, tzinfo=UTC))
    return sink, container, ingest


def test_sink_keeps_raw_first_then_tables_then_the_batch_marker(sink_parts):
    sink, container, ingest = sink_parts
    body = batch([*captured_lines(hours=1.0), "{bad"])
    reply = sink.send(body)
    assert container.blobs == {blob_name(body): body}  # byte for byte
    tables = [table for table, _, _ in ingest.calls]
    assert tables == ["Environment", "Od", "Rejected", "Batches"]
    assert all(mapping == f"{table}Mapping" for table, mapping, _ in ingest.calls)
    marker = ingest.calls[-1][2][0]
    assert marker["rejected"] == 1 and marker["rows"] == reply["stored"]
    assert reply["lines"] == reply["stored"] + 1
    assert reply["rejected"][0]["reason"] == "invalid_json"


def test_sink_failure_is_retryable(sink_parts):
    sink, _, _ = sink_parts
    sink.container = FakeContainer(fail=True)
    with pytest.raises(IngestUnavailable):
        sink.send(batch(captured_lines(hours=0.5)))


def test_uploader_sends_through_the_sink_and_advances_its_cursor(tmp_path, sink_parts):
    sink, container, _ = sink_parts
    log = tmp_path / "telemetry.jsonl"
    log.write_text("\n".join(captured_lines(hours=1.0)) + "\n")
    uploader = Uploader(log, "http://unused", sender=sink.send, batch_lines=5)
    sent = uploader.drain()
    assert sum(s.stored for s in sent) == len(log.read_text().splitlines())
    assert uploader.pending_lines() == 0 and len(container.blobs) == len(sent)


def test_permission_errors_stop_instead_of_retrying(sink_parts):
    from algae_sim.azure.sink import is_permanent
    from algae_sim.pipeline.uploader import IngestRefused

    class Forbidden(Exception):
        status_code = 403

    sink, _, _ = sink_parts

    class Refusing(FakeContainer):
        def upload_blob(self, name, data, overwrite):
            raise Forbidden("AuthorizationPermissionMismatch")

    sink.container = Refusing()
    with pytest.raises(IngestRefused):
        sink.send(batch(captured_lines(hours=0.5)))
    assert is_permanent(Exception('Forbidden (403-Forbidden): ... "@permanent": true'))
    assert not is_permanent(ConnectionError("reset by peer"))
