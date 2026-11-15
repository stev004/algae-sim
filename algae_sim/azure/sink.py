"""Send one batch to Azure: raw to Blob Storage, clean rows to Data Explorer.

Plugs into ``algae_sim.pipeline.uploader.Uploader`` as its ``sender``, in place of
the local HTTP ingest, and returns the same reply shape. The uploader's cursor,
retry and backoff therefore work unchanged:

* raw first: the batch is stored byte for byte, under a content-hash name, so a
  resend overwrites the same blob instead of adding a copy;
* then each table's records are queued for ingestion into Data Explorer;
* the ``Batches`` record goes last, as the marker that the batch is complete.

Queued, not streaming, ingestion: on the free cluster, streaming writes from an
Entra ID (Azure directory) user are refused with 403 even with the Ingestor role,
while queued ingestion with the same role works. Rows appear after the database's
batching window (set to 30 s in schema.kql) rather than within a second, which is
fine for batched lab data.

A transient failure (network, throttling, service hiccup) raises
``IngestUnavailable``, so the uploader keeps its cursor and retries. A partly
ingested batch is harmless: the resend lands again and the materialised views keep
one row per (device_id, sequence). A *permanent* failure, such as a missing
permission (401/403), raises ``IngestRefused`` instead: retrying can't fix it, so
the uploader stops with the reason rather than looping quietly.
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
from algae_sim.pipeline.uploader import IngestRefused, IngestUnavailable


@dataclass(frozen=True, slots=True)
class AzureConfig:
    """Where to send data. Addresses only; there are no secrets in here."""

    tenant_id: str  # the directory the free account signed in to (Entra ID > Overview)
    storage_account: str
    cluster_uri: str
    container: str = "raw"
    database: str = DATABASE_DEFAULT
    ingest_uri: str | None = None  # default: the cluster URI with "ingest-" in front

    @property
    def ingestion_uri(self) -> str:
        return self.ingest_uri or self.cluster_uri.replace("https://", "https://ingest-", 1)

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
                    self._ingest(table, to_ndjson(records))
            self._ingest("Batches", to_ndjson([{
                "blob_name": blob_name(body),
                "lines": sum(1 for line in body.splitlines() if line.strip()),
                "rows": len(tables["Environment"]) + len(tables["Od"]),
                "rejected": len(rejected),
                "batch_id": batch_id(body),
                "ingested_at": ingested_at,
            }]))
        except Exception as exc:
            message = f"azure: {type(exc).__name__}: {_short(exc)}"
            if is_permanent(exc):
                raise IngestRefused(message) from exc
            raise IngestUnavailable(message) from exc
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

    def _ingest(self, table: str, data: bytes) -> None:
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


def _status(exc: BaseException) -> int | None:
    for holder in (exc, getattr(exc, "response", None), getattr(exc, "http_response", None)):
        code = getattr(holder, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def is_permanent(exc: BaseException) -> bool:
    """True for failures a retry can't fix: refused credentials or permissions."""
    if type(exc).__name__ in ("ClientAuthenticationError", "KustoAuthenticationError"):
        return True
    if _status(exc) in (401, 403):
        return True
    text = str(exc)
    return "403-Forbidden" in text or '"@permanent": true' in text


def _short(exc: BaseException) -> str:
    """First line of an SDK error, plus the principal it names, if any."""
    import re

    text = str(exc)
    principal = re.search(r"Principal '([^']+)'", text)
    first = text.strip().splitlines()[0][:200] if text.strip() else ""
    return first + (f" [principal {principal.group(1)}]" if principal else "")


AUTH_RECORD = Path.home() / ".config" / "algae-sim" / "azure-auth-record.json"


def credential(tenant_id: str) -> Any:
    """A browser sign-in that's remembered between runs.

    The token cache lives in the OS keychain (msal persistence), and the
    authentication record (which account, not a secret) in ~/.config/algae-sim.
    The first run opens the browser; later runs reuse the cached sign-in until it
    expires.
    """
    from azure.identity import (
        AuthenticationRecord,
        InteractiveBrowserCredential,
        TokenCachePersistenceOptions,
    )

    cache = TokenCachePersistenceOptions(name="algae-sim")
    record = None
    if AUTH_RECORD.exists():
        record = AuthenticationRecord.deserialize(AUTH_RECORD.read_text())
    cred = InteractiveBrowserCredential(
        tenant_id=tenant_id, cache_persistence_options=cache, authentication_record=record
    )
    if record is None:
        record = cred.authenticate(scopes=["https://storage.azure.com/.default"])
        AUTH_RECORD.parent.mkdir(parents=True, exist_ok=True)
        AUTH_RECORD.write_text(record.serialize())
    return cred


def _clients(config: AzureConfig) -> tuple[Any, Any]:
    """One browser sign-in, shared by Blob Storage and Data Explorer."""
    from azure.kusto.data import KustoConnectionStringBuilder
    from azure.kusto.ingest import QueuedIngestClient
    from azure.storage.blob import BlobServiceClient

    shared = credential(config.tenant_id)
    blobs = BlobServiceClient(
        account_url=f"https://{config.storage_account}.blob.core.windows.net",
        credential=shared,
    )
    container = blobs.get_container_client(config.container)
    kusto = KustoConnectionStringBuilder.with_azure_token_credential(config.ingestion_uri, shared)
    return container, QueuedIngestClient(kusto)
