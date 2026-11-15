"""Send one batch to Azure: raw to Blob Storage, clean rows to Data Explorer.

Plugs into ``algae_sim.pipeline.uploader.Uploader`` as its ``sender``, in place of
the local HTTP ingest, and returns the same reply shape. The uploader's cursor,
retry and backoff therefore work unchanged:

* raw first: the batch is stored byte for byte, under a content-hash name, so a
  resend overwrites the same blob instead of adding a copy;
* then each table's records are streamed into Data Explorer;
* the ``Batches`` record goes last, as the marker that the batch is complete.

Any failure raises ``IngestUnavailable``, so the uploader keeps its cursor and
retries. A partly ingested batch is harmless: the resend lands again and the
materialised views keep one row per (device_id, sequence).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from algae_sim.azure.records import (
    DATABASE_DEFAULT,
    batch_id,
    blob_name,
    mapping_name,
    split_batch,
    to_ndjson,
)
from algae_sim.pipeline.uploader import IngestUnavailable

STREAMING_LIMIT_BYTES = 4 * 1024 * 1024  # Data Explorer streaming ingestion, per request


@dataclass(frozen=True, slots=True)
class AzureConfig:
    """Where to send data. Addresses only; there are no secrets in here."""

    tenant_id: str  # the directory the free account signed in to (Entra ID > Overview)
    storage_account: str
    cluster_uri: str
    container: str = "raw"
    database: str = DATABASE_DEFAULT

    @classmethod
    def load(cls, path: Path) -> AzureConfig:
        return cls(**json.loads(Path(path).read_text()))


class AzureSink:
    def __init__(
        self,
        config: AzureConfig,
        *,
        container: Any = None,
        ingest: Any = None,
        clock: Any = None,
    ) -> None:
        """``container`` and ``ingest`` can be fakes in tests; by default they're built
        from one interactive browser sign-in shared by both services."""
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))
        if container is None or ingest is None:
            built_container, built_ingest = _clients(config)
            container = container or built_container
            ingest = ingest or built_ingest
        self.container = container
        self.ingest = ingest

    def send(self, body: bytes) -> dict[str, Any]:
        ingested_at = self.clock().isoformat()
        tables, rejected = split_batch(body, ingested_at)
        try:
            self.container.upload_blob(name=blob_name(body), data=body, overwrite=True)
            for table, records in tables.items():
                if records:
                    self._stream(table, to_ndjson(records))
            self._stream("Batches", to_ndjson([{
                "blob_name": blob_name(body),
                "lines": sum(1 for line in body.splitlines() if line.strip()),
                "rows": len(tables["Environment"]) + len(tables["Od"]),
                "rejected": len(rejected),
                "batch_id": batch_id(body),
                "ingested_at": ingested_at,
            }]))
        except Exception as exc:  # any SDK or network failure: keep the cursor, retry
            raise IngestUnavailable(f"azure: {type(exc).__name__}: {exc}") from exc
        stored = len(tables["Environment"]) + len(tables["Od"])
        return {
            "batch_id": batch_id(body),
            "lines": stored + len(rejected),
            "stored": stored,
            # Data Explorer accepts duplicates on ingest; the *Clean views remove them,
            # and PipelineHealth() reports how many.
            "duplicates": 0,
            "rejected": rejected,
        }

    def _stream(self, table: str, data: bytes) -> None:
        if len(data) > STREAMING_LIMIT_BYTES:
            raise ValueError(f"{table}: {len(data)} bytes exceeds the streaming limit")
        from io import BytesIO

        from azure.kusto.data import DataFormat
        from azure.kusto.ingest import IngestionProperties

        properties = IngestionProperties(
            database=self.config.database,
            table=table,
            data_format=DataFormat.MULTIJSON,
            ingestion_mapping_reference=mapping_name(table),
        )
        self.ingest.ingest_from_stream(BytesIO(data), ingestion_properties=properties)


def _clients(config: AzureConfig) -> tuple[Any, Any]:
    """One browser sign-in, shared by Blob Storage and Data Explorer."""
    from azure.identity import InteractiveBrowserCredential
    from azure.kusto.data import KustoConnectionStringBuilder
    from azure.kusto.ingest import KustoStreamingIngestClient
    from azure.storage.blob import BlobServiceClient

    credential = InteractiveBrowserCredential(tenant_id=config.tenant_id)
    blobs = BlobServiceClient(
        account_url=f"https://{config.storage_account}.blob.core.windows.net",
        credential=credential,
    )
    container = blobs.get_container_client(config.container)
    kusto = KustoConnectionStringBuilder.with_azure_token_credential(
        config.cluster_uri, credential
    )
    return container, KustoStreamingIngestClient(kusto)
