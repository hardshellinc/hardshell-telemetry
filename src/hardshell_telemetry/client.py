"""REST client for the Hardshell API.

This is the thin transport layer: authentication plus well-typed methods
that mirror the public endpoints one-to-one. It has no dependencies beyond
the Python standard library.

    from hardshell_telemetry import TelemetryClient

    client = TelemetryClient(
        api_key="hs-...",
        base_url="https://<your-hardshell-endpoint>",
    )

    # After a vector-store query (top-k results, scores normalized to [0, 1]):
    client.record_retrieval(
        chunks=[("doc-42:chunk-3", 0.91), ("doc-42:chunk-7", 0.88)],
        user_id="end-user-123",
        backend="chroma",
    )

Your organization is derived from the API key server-side — you never send a
tenant or org id. Failed requests raise :class:`TelemetryError` by default so
setup problems are visible; see that class for the non-fatal production
pattern.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from hardshell_telemetry.exceptions import TelemetryError
from hardshell_telemetry.types import (
    Chunk,
    Document,
    DocumentAccessReport,
    IngestChunksResult,
    IngestDocumentsResult,
    IngestSpansResult,
    RetrievalSpan,
    RetrievedChunkLike,
    iso_timestamp,
)

__all__ = ["TelemetryClient"]


def _payload_of(item: Any) -> dict[str, Any]:
    """Serialize a typed payload; pass plain dicts through verbatim."""
    if hasattr(item, "to_payload"):
        return item.to_payload()
    return dict(item)


class TelemetryClient:
    """Client for sending telemetry to Hardshell and reading reports back.

    Args:
        api_key: Your Hardshell API key. Sent as a bearer token; your
            organization is derived from it server-side.
        base_url: Your Hardshell endpoint, e.g. from your onboarding. The
            interactive API reference lives at ``<base_url>/docs``.
        timeout: Per-request timeout in seconds.
        source: Default provenance label stamped on everything this client
            sends when a call doesn't set its own — e.g. ``"production"``,
            ``"staging"``, ``"evaluation"``. Empty means unlabeled.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 5.0,
        source: str = "",
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not base_url:
            raise ValueError("base_url is required (your Hardshell endpoint)")
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._source = source

    # ── Enrichment data plane (index-build time) ────────────────────────────
    # Register documents and chunks once per corpus build (or when they
    # change), not per query. Hardshell joins retrieval spans against this
    # metadata by document_id / chunk_id.

    def ingest_documents(
        self,
        documents: Sequence[Document | dict[str, Any]],
        *,
        source: str = "",
    ) -> IngestDocumentsResult:
        """Upsert source-document metadata (``POST /v1/documents``)."""
        payload = {
            "documents": [_payload_of(d) for d in documents],
            "source": source or self._source,
        }
        return IngestDocumentsResult.from_payload(self._post("/v1/documents", payload))

    def ingest_chunks(
        self,
        chunks: Sequence[Chunk | dict[str, Any]],
        *,
        source: str = "",
    ) -> IngestChunksResult:
        """Upsert per-chunk metadata (``POST /v1/chunks``)."""
        payload = {
            "chunks": [_payload_of(c) for c in chunks],
            "source": source or self._source,
        }
        return IngestChunksResult.from_payload(self._post("/v1/chunks", payload))

    # ── Retrieval data plane (query time) ───────────────────────────────────

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
        source: str = "",
    ) -> IngestSpansResult:
        """Send a single retrieval event — the common case.

        Call this after each vector-store query with the chunks it returned.
        Chunks can be ``(chunk_id, score)`` tuples, :class:`RetrievedChunk`
        instances, dicts, or bare chunk-id strings.
        """
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
        )
        return self.ingest_spans([span])

    def ingest_spans(
        self,
        spans: Sequence[RetrievalSpan | dict[str, Any]],
    ) -> IngestSpansResult:
        """Send one or more retrieval spans (``POST /v1/spans``).

        Spans with an empty ``source`` inherit the client's default source.
        """
        serialized = []
        for span in spans:
            payload = _payload_of(span)
            if not payload.get("source") and self._source:
                payload["source"] = self._source
            serialized.append(payload)
        return IngestSpansResult.from_payload(
            self._post("/v1/spans", {"spans": serialized})
        )

    # ── Reports (read your own derived data back) ───────────────────────────

    def document_access_report(
        self,
        *,
        window_start: datetime | str | None = None,
        window_end: datetime | str | None = None,
        limit: int = 0,
        offset: int = 0,
    ) -> DocumentAccessReport:
        """How often your chunks are retrieved, grouped by document
        (``GET /v1/reports/document-access``).

        Args:
            window_start: Only count retrievals at or after this time.
            window_end: Only count retrievals before this time.
            limit: Page size; 0 means the server default.
            offset: Page offset for walking large corpora.
        """
        params: dict[str, str] = {}
        if window_start is not None:
            params["window_start"] = _time_param(window_start)
        if window_end is not None:
            params["window_end"] = _time_param(window_end)
        if limit:
            params["limit"] = str(limit)
        if offset:
            params["offset"] = str(offset)
        return DocumentAccessReport.from_payload(
            self._get("/v1/reports/document-access", params)
        )

    # ── Transport ────────────────────────────────────────────────────────────

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request("POST", path, body=body)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if params:
            path = path + "?" + urllib.parse.urlencode(params)
        return self._request("GET", path)

    def _request(self, method: str, path: str, body: bytes | None = None) -> dict[str, Any]:
        from hardshell_telemetry import __version__

        request = urllib.request.Request(
            self._base + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer " + self._api_key,
                "Content-Type": "application/json",
                "User-Agent": f"hardshell-telemetry/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise TelemetryError(
                f"hardshell request failed: HTTP {exc.code} {detail}",
                status_code=exc.code,
                detail=detail,
            ) from exc
        except urllib.error.URLError as exc:
            raise TelemetryError(f"hardshell request failed: {exc.reason}") from exc


def _time_param(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return iso_timestamp(value)
    return value
