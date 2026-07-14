"""Typed payloads for the Hardshell API.

These dataclasses mirror the public REST contract one-to-one — see the
``/docs`` page on your Hardshell endpoint for the authoritative schema.
Every ingest method also accepts plain ``dict`` payloads, which are sent
verbatim; the types are here to help, not to get in the way.

Optional fields left as ``None`` are omitted from the request entirely, so
the server applies its own defaults.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "Chunk",
    "ChunkAccessCount",
    "Document",
    "DocumentAccessReport",
    "DocumentAccessSummary",
    "DocumentLink",
    "IngestChunksResult",
    "IngestDocumentsResult",
    "IngestSpansResult",
    "RetrievalSpan",
    "RetrievedChunk",
    "RetrievedChunkLike",
]


def iso_timestamp(dt: datetime) -> str:
    """Format a datetime as ISO 8601; naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _without_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None}


# ── Ingest inputs ────────────────────────────────────────────────────────────


@dataclass
class Document:
    """Source-document metadata for ``POST /v1/documents``.

    ``document_id`` is your id for the document — an id you choose, never its
    contents. All other fields are optional context that makes reports and
    detection richer:

    - ``name``: human-readable title or path, shown for convenience.
    - ``content_hash``: fingerprint of the contents (e.g. sha256 hex) so
      changes are detectable across re-ingests.
    - ``sensitivity``: how sensitive the document is, on your own 0–1 scale.
    - ``sensitivity_level``: a sensitivity label of your choosing,
      e.g. ``"confidential"``.
    - ``custom_metadata``: any other fields to keep with the document
      (free-form JSON, searchable later).
    - ``simhash_hex``: optional similarity fingerprint, if your indexer
      produces one.
    """

    document_id: str
    name: str | None = None
    content_hash: str | None = None
    sensitivity: float | None = None
    sensitivity_level: str | None = None
    custom_metadata: dict[str, Any] | None = None
    simhash_hex: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return _without_none(
            {
                "document_id": self.document_id,
                "name": self.name,
                "content_hash": self.content_hash,
                "sensitivity": self.sensitivity,
                "sensitivity_level": self.sensitivity_level,
                "custom_metadata": self.custom_metadata,
                "simhash_hex": self.simhash_hex,
            }
        )


@dataclass
class DocumentLink:
    """A chunk → source-document link, carried on :class:`Chunk`.

    ``link_metadata`` is optional detail about the link, e.g. how much of the
    chunk came from this document.
    """

    document_id: str
    link_metadata: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        return _without_none({"document_id": self.document_id, "link_metadata": self.link_metadata})


@dataclass
class Chunk:
    """Per-chunk metadata for ``POST /v1/chunks``.

    ``chunk_id`` is your id for the chunk — **the same id you report when the
    chunk is retrieved**. If the ids don't match, retrievals can't be joined
    back to this metadata.

    - ``sensitivity`` / ``sensitivity_level``: as on :class:`Document`.
    - ``pii_flags``: any PII indicators for the chunk (free-form JSON).
    - ``taxonomy``: your own classification tags (free-form JSON).
    - ``custom_metadata``: anything else to keep with the chunk.
    - ``document_links``: the documents this chunk came from; a chunk may
      come from more than one.
    - ``simhash_hex``: optional similarity fingerprint.
    """

    chunk_id: str
    sensitivity: float | None = None
    sensitivity_level: str | None = None
    pii_flags: dict[str, Any] | None = None
    taxonomy: dict[str, Any] | None = None
    custom_metadata: dict[str, Any] | None = None
    document_links: Sequence[DocumentLink | dict[str, Any]] = ()
    simhash_hex: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = _without_none(
            {
                "chunk_id": self.chunk_id,
                "sensitivity": self.sensitivity,
                "sensitivity_level": self.sensitivity_level,
                "pii_flags": self.pii_flags,
                "taxonomy": self.taxonomy,
                "custom_metadata": self.custom_metadata,
                "simhash_hex": self.simhash_hex,
            }
        )
        if self.document_links:
            payload["document_links"] = [
                link.to_payload() if isinstance(link, DocumentLink) else dict(link)
                for link in self.document_links
            ]
        return payload


@dataclass
class RetrievedChunk:
    """One chunk returned by a retrieval, for :class:`RetrievalSpan`.

    ``chunk_id`` must be the same id the chunk was registered under.
    ``score`` is the similarity score, if you have one.
    """

    chunk_id: str
    score: float = 0.0

    def to_payload(self) -> dict[str, Any]:
        return {"chunk_id": self.chunk_id, "score": float(self.score)}


# A retrieved chunk in any convenient shape: a RetrievedChunk, a
# (chunk_id, score) tuple, a {"chunk_id": ..., "score": ...} dict, or a bare
# chunk-id string (score defaults to 0.0).
RetrievedChunkLike = RetrievedChunk | tuple[str, float] | dict[str, Any] | str


def retrieved_chunk_payload(chunk: RetrievedChunkLike) -> dict[str, Any]:
    """Normalize any :data:`RetrievedChunkLike` into the wire format."""
    if isinstance(chunk, RetrievedChunk):
        return chunk.to_payload()
    if isinstance(chunk, str):
        return {"chunk_id": chunk, "score": 0.0}
    if isinstance(chunk, dict):
        return {"chunk_id": chunk["chunk_id"], "score": float(chunk.get("score", 0.0))}
    chunk_id, score = chunk
    return {"chunk_id": chunk_id, "score": float(score)}


@dataclass
class RetrievalSpan:
    """One retrieval event (a single vector-store search) for ``POST /v1/spans``.

    - ``chunks``: the chunks this search returned.
    - ``backend``: which vector store served it, e.g. ``"chroma"``.
    - ``timestamp``: when the search happened; defaults to now (UTC) at send
      time when left as ``None``.
    - ``user_id`` / ``session_id`` / ``ip``: the end user behind the request,
      if known — this is what per-identity detection keys on.
    - ``trace_id`` / ``span_id``: your own correlation ids, if you have them.
    - ``attributes``: any extra tags to attach (free-form JSON, searchable).
    - ``source``: where this traffic came from, e.g. ``"production"``;
      defaults to the client's ``source`` when left empty.
    """

    chunks: Sequence[RetrievedChunkLike] = ()
    backend: str = ""
    timestamp: datetime | None = None
    user_id: str = ""
    session_id: str = ""
    ip: str = ""
    trace_id: str = ""
    span_id: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "timestamp": iso_timestamp(self.timestamp or datetime.now(UTC)),
            "user_id": self.user_id,
            "session_id": self.session_id,
            "ip": self.ip,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "chunks": [retrieved_chunk_payload(c) for c in self.chunks],
            "attributes": dict(self.attributes),
            "source": self.source,
        }


# ── Ingest results ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IngestDocumentsResult:
    """Response of ``POST /v1/documents``."""

    documents_upserted: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> IngestDocumentsResult:
        return cls(documents_upserted=int(payload.get("documents_upserted", 0)))


@dataclass(frozen=True)
class IngestChunksResult:
    """Response of ``POST /v1/chunks``."""

    chunks_upserted: int
    links_upserted: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> IngestChunksResult:
        return cls(
            chunks_upserted=int(payload.get("chunks_upserted", 0)),
            links_upserted=int(payload.get("links_upserted", 0)),
        )


@dataclass(frozen=True)
class IngestSpansResult:
    """Response of ``POST /v1/spans``."""

    spans_accepted: int
    chunks_logged: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> IngestSpansResult:
        return cls(
            spans_accepted=int(payload.get("spans_accepted", 0)),
            chunks_logged=int(payload.get("chunks_logged", 0)),
        )


# ── Report outputs ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChunkAccessCount:
    """One chunk's retrieval count within the report window."""

    chunk_id: str
    access_count: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ChunkAccessCount:
        return cls(
            chunk_id=payload["chunk_id"],
            access_count=int(payload["access_count"]),
        )


@dataclass(frozen=True)
class DocumentAccessSummary:
    """How often one document's chunks were retrieved in the window."""

    document_id: str
    name: str
    chunk_count: int
    chunks: tuple[ChunkAccessCount, ...]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DocumentAccessSummary:
        return cls(
            document_id=payload["document_id"],
            name=payload.get("name", ""),
            chunk_count=int(payload["chunk_count"]),
            chunks=tuple(ChunkAccessCount.from_payload(c) for c in payload.get("chunks", [])),
        )


@dataclass(frozen=True)
class DocumentAccessReport:
    """Response of ``GET /v1/reports/document-access``.

    ``total_documents`` is the total number of documents you've registered,
    independent of any ``limit``/``offset`` paging applied to this response.
    """

    documents: tuple[DocumentAccessSummary, ...]
    total_documents: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DocumentAccessReport:
        return cls(
            documents=tuple(
                DocumentAccessSummary.from_payload(d) for d in payload.get("documents", [])
            ),
            total_documents=int(payload.get("total_documents", 0)),
        )
