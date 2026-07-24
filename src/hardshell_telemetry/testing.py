"""In-memory test double for :class:`~hardshell_telemetry.TelemetryClient`.

Inject :class:`MockTelemetryClient` wherever your code depends on a telemetry
client, exercise your code, then assert on what it recorded — no network and no
fake server. The ``make_*`` factories build valid payloads and reports with
sane defaults so tests stay short.

    from hardshell_telemetry.testing import MockTelemetryClient

    telemetry = MockTelemetryClient()
    SearchService(telemetry).search("hr policy", user_id="u-1")   # your code

    span = telemetry.spans[0]
    assert span.user_id == "u-1"
    assert span.corpus == "qdrant:docs"

To test code that *reads* reports, seed one with :func:`make_report`:

    seeded = make_report([make_summary("handbook", corpora=[CorpusAccessCount("qdrant:docs", 3)])])
    telemetry = MockTelemetryClient(report=seeded)
    assert usage_dashboard(telemetry).top_document() == "handbook"

This module imports nothing beyond the package itself; it is intentionally not
re-exported from the top level, so production code never picks it up by
accident — import it explicitly from ``hardshell_telemetry.testing``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hardshell_telemetry.client import TelemetryClient
from hardshell_telemetry.types import (
    Chunk,
    ChunkAccessCount,
    CorpusAccessCount,
    Document,
    DocumentAccessReport,
    DocumentAccessSummary,
    DocumentLink,
    IngestChunksResult,
    IngestDocumentsResult,
    IngestSpansResult,
    RetrievalSpan,
    RetrievedChunk,
    RetrievedChunkLike,
)

__all__ = [
    "IngestChunksCall",
    "IngestDocumentsCall",
    "MockTelemetryClient",
    "ReportQuery",
    "make_chunk",
    "make_document",
    "make_report",
    "make_span",
    "make_summary",
]


@dataclass
class IngestDocumentsCall:
    """One recorded ``ingest_documents`` call: the items and the request-level
    ``source``/``corpus`` they were sent with."""

    documents: list[Document | dict[str, Any]]
    source: str | None
    corpus: str | None


@dataclass
class IngestChunksCall:
    """One recorded ``ingest_chunks`` call: the items and the request-level
    ``source``/``corpus`` they were sent with."""

    chunks: list[Chunk | dict[str, Any]]
    source: str | None
    corpus: str | None


@dataclass
class ReportQuery:
    """One recorded ``document_access_report`` query — the window and paging the
    caller asked for."""

    window_start: datetime | str | None = None
    window_end: datetime | str | None = None
    limit: int | None = None
    offset: int | None = None


def _link_count(chunk: Chunk | dict[str, Any]) -> int:
    """How many document links a chunk carries (typed or raw dict)."""
    if isinstance(chunk, Chunk):
        return len(chunk.document_links)
    return len(chunk.get("document_links", []))


def _chunk_count(span: RetrievalSpan | dict[str, Any]) -> int:
    """How many chunks a span returned (typed or raw dict)."""
    if isinstance(span, RetrievalSpan):
        return len(list(span.chunks))
    return len(span.get("chunks", []))


class MockTelemetryClient(TelemetryClient):
    """An in-memory :class:`~hardshell_telemetry.TelemetryClient` for tests.

    Records every call and sends nothing. Inspect what your code sent through
    :attr:`documents`, :attr:`chunks`, and :attr:`spans` (flattened across
    calls, in call order), or the per-call logs
    :attr:`ingest_documents_calls` / :attr:`ingest_chunks_calls` /
    :attr:`ingest_spans_calls` when you need the request-level
    ``source``/``corpus``. Ingest methods return results with truthful counts;
    :meth:`document_access_report` returns whatever report you seed.

    Args:
        report: The report :meth:`document_access_report` returns (build one
            with :func:`make_report`). Defaults to an empty report; you can
            also reassign :attr:`report` between calls.
    """

    def __init__(self, *, report: DocumentAccessReport | None = None) -> None:
        self.report = report if report is not None else DocumentAccessReport((), 0)
        self.ingest_documents_calls: list[IngestDocumentsCall] = []
        self.ingest_chunks_calls: list[IngestChunksCall] = []
        self.ingest_spans_calls: list[list[RetrievalSpan | dict[str, Any]]] = []
        self.report_queries: list[ReportQuery] = []

    @property
    def documents(self) -> list[Document | dict[str, Any]]:
        """Every document ingested, flattened across calls in call order."""
        return [d for call in self.ingest_documents_calls for d in call.documents]

    @property
    def chunks(self) -> list[Chunk | dict[str, Any]]:
        """Every chunk ingested, flattened across calls in call order."""
        return [c for call in self.ingest_chunks_calls for c in call.chunks]

    @property
    def spans(self) -> list[RetrievalSpan | dict[str, Any]]:
        """Every span recorded, flattened across calls in call order."""
        return [s for batch in self.ingest_spans_calls for s in batch]

    def ingest_documents(
        self,
        documents: Sequence[Document | dict[str, Any]],
        *,
        source: str | None = None,
        corpus: str | None = None,
    ) -> IngestDocumentsResult:
        """Record the call; return a result counting the documents."""
        items = list(documents)
        self.ingest_documents_calls.append(IngestDocumentsCall(items, source, corpus))
        return IngestDocumentsResult(documents_upserted=len(items))

    def ingest_chunks(
        self,
        chunks: Sequence[Chunk | dict[str, Any]],
        *,
        source: str | None = None,
        corpus: str | None = None,
    ) -> IngestChunksResult:
        """Record the call; return a result counting the chunks and their links."""
        items = list(chunks)
        self.ingest_chunks_calls.append(IngestChunksCall(items, source, corpus))
        return IngestChunksResult(
            chunks_upserted=len(items),
            links_upserted=sum(_link_count(c) for c in items),
        )

    def record_retrieval(
        self,
        chunks: Sequence[RetrievedChunkLike],
        *,
        backend: str = "",
        user_id: str = "",
        session_id: str = "",
        ip: str = "",
        trace_id: str = "",
        span_id: str = "",
        timestamp: datetime | None = None,
        attributes: dict[str, Any] | None = None,
        source: str | None = None,
        corpus: str | None = None,
    ) -> IngestSpansResult:
        """Build a span from the arguments and route it through :meth:`ingest_spans`,
        exactly as the real client does — so :attr:`spans` sees it either way."""
        span = RetrievalSpan(
            chunks=chunks,
            backend=backend,
            timestamp=timestamp,
            user_id=user_id,
            session_id=session_id,
            ip=ip,
            trace_id=trace_id,
            span_id=span_id,
            attributes=attributes or {},
            source=source,
            corpus=corpus,
        )
        return self.ingest_spans([span])

    def ingest_spans(
        self,
        spans: Sequence[RetrievalSpan | dict[str, Any]],
    ) -> IngestSpansResult:
        """Record the batch; return a result counting spans and their chunks."""
        batch = list(spans)
        self.ingest_spans_calls.append(batch)
        return IngestSpansResult(
            spans_accepted=len(batch),
            chunks_logged=sum(_chunk_count(s) for s in batch),
        )

    def document_access_report(
        self,
        *,
        window_start: datetime | str | None = None,
        window_end: datetime | str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> DocumentAccessReport:
        """Record the query; return the seeded :attr:`report` (empty by default)."""
        self.report_queries.append(ReportQuery(window_start, window_end, limit, offset))
        return self.report


# ── data factories ────────────────────────────────────────────────────────────


def make_document(document_id: str = "doc-1", **fields: Any) -> Document:
    """A :class:`~hardshell_telemetry.Document` with a default id; override any
    other field via keyword (``name=``, ``sensitivity=``, …)."""
    return Document(document_id=document_id, **fields)


def make_chunk(
    chunk_id: str = "doc-1:0", *, document_id: str | None = "doc-1", **fields: Any
) -> Chunk:
    """A :class:`~hardshell_telemetry.Chunk` linked to ``document_id`` by default.

    Pass ``document_links=`` to set links yourself, or ``document_id=None`` for
    an unlinked chunk. Any other :class:`~hardshell_telemetry.Chunk` field can
    be overridden by keyword.
    """
    if "document_links" not in fields and document_id is not None:
        fields["document_links"] = [DocumentLink(document_id=document_id)]
    return Chunk(chunk_id=chunk_id, **fields)


def make_span(
    chunk_ids: Sequence[str] = ("doc-1:0",),
    *,
    score: float = 0.9,
    backend: str = "qdrant",
    **fields: Any,
) -> RetrievalSpan:
    """A :class:`~hardshell_telemetry.RetrievalSpan` that returned ``chunk_ids``
    (each at ``score``); override any other field by keyword (``user_id=``,
    ``corpus=``, …)."""
    chunks = [RetrievedChunk(chunk_id=cid, score=score) for cid in chunk_ids]
    return RetrievalSpan(chunks=chunks, backend=backend, **fields)


def make_summary(
    document_id: str = "doc-1",
    *,
    name: str = "",
    chunk_count: int | None = None,
    chunks: Sequence[ChunkAccessCount] = (),
    corpora: Sequence[CorpusAccessCount] = (),
) -> DocumentAccessSummary:
    """A :class:`~hardshell_telemetry.DocumentAccessSummary` for seeding a report.

    ``chunk_count`` defaults to the number of ``chunks`` given.
    """
    chunks_t = tuple(chunks)
    return DocumentAccessSummary(
        document_id=document_id,
        name=name,
        chunk_count=chunk_count if chunk_count is not None else len(chunks_t),
        chunks=chunks_t,
        corpora=tuple(corpora),
    )


def make_report(
    documents: Sequence[DocumentAccessSummary] = (),
    *,
    total_documents: int | None = None,
) -> DocumentAccessReport:
    """A :class:`~hardshell_telemetry.DocumentAccessReport` to seed
    :class:`MockTelemetryClient`. ``total_documents`` defaults to the number of
    ``documents`` given."""
    docs = tuple(documents)
    return DocumentAccessReport(
        documents=docs,
        total_documents=total_documents if total_documents is not None else len(docs),
    )
