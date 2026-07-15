"""REST client for the Hardshell API.

This is the thin transport layer: authentication plus well-typed methods
that mirror the public endpoints one-to-one. It has no dependencies beyond
the Python standard library.

    from hardshell_telemetry import HardshellClient

    client = HardshellClient(
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
tenant or org id. Every failure raises :class:`TelemetryError` — transport
errors, timeouts, non-2xx responses, and non-JSON bodies — so setup problems
are visible; see that class for the non-fatal production pattern. Redirects
are never followed (``base_url`` must be the final endpoint): following one
would replay the request elsewhere and expose the API key to the redirect
target.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from hardshell_telemetry._version import __version__
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

__all__ = ["HardshellClient"]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would convert POST to a body-less GET and
    re-send the Authorization header to the redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirects())


def _payload_of(item: Any) -> dict[str, Any]:
    """Serialize a typed payload; pass plain dicts through verbatim."""
    if hasattr(item, "to_payload"):
        return item.to_payload()
    return dict(item)


class HardshellClient:
    """Client for sending telemetry to Hardshell and reading reports back.

    Args:
        api_key: Your Hardshell API key. Sent as a bearer token; your
            organization is derived from it server-side.
        base_url: Your Hardshell endpoint, e.g. from your onboarding. The
            interactive API reference lives at ``<base_url>/docs``.
        timeout: Per-request timeout in seconds.
        source: Default provenance label for everything this client sends —
            *where is this traffic coming from?* Free-form; common values are
            ``"production"``, ``"staging"``, ``"testing"``, ``"simulation"``,
            ``"evaluation"``. Hardshell uses it to keep experimental traffic
            out of production detection baselines and to filter reports.

            Resolution order for each payload: an explicit per-call/per-span
            ``source`` wins, then this client default, then the server-side
            default attached to your API key (Hardshell can issue
            environment-scoped keys), and otherwise the traffic is stored
            unlabeled. Leave this as ``None`` if your API key is
            environment-scoped — any value you send overrides the key's
            default. Pass ``""`` at call level to force a payload through
            unlabeled. Raw dict spans are never modified.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 5.0,
        source: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Hardshell api_key is required")
        if not base_url:
            raise ValueError("Hardshell base_url is required (your Hardshell endpoint)")
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._source = source
        self._headers = {
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": f"hardshell-telemetry/{__version__}",
        }

    # ── Enrichment data plane (index-build time) ────────────────────────────
    # Register documents and chunks once per corpus build (or when they
    # change), not per query. Hardshell joins retrieval spans against this
    # metadata by document_id / chunk_id.

    def ingest_documents(
        self,
        documents: Sequence[Document | dict[str, Any]],
        *,
        source: str | None = None,
    ) -> IngestDocumentsResult:
        """Upsert source-document metadata (``POST /v1/documents``).

        ``source`` labels the provenance of this push (see
        :class:`HardshellClient`); ``None`` inherits the client default.
        """
        return IngestDocumentsResult.from_payload(
            self._post_batch("/v1/documents", "documents", documents, source)
        )

    def ingest_chunks(
        self,
        chunks: Sequence[Chunk | dict[str, Any]],
        *,
        source: str | None = None,
    ) -> IngestChunksResult:
        """Upsert per-chunk metadata (``POST /v1/chunks``).

        ``source`` labels the provenance of this push (see
        :class:`HardshellClient`); ``None`` inherits the client default.
        """
        return IngestChunksResult.from_payload(
            self._post_batch("/v1/chunks", "chunks", chunks, source)
        )

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
        source: str | None = None,
    ) -> IngestSpansResult:
        """Send a single retrieval event — the common case.

        Call this after each vector-store query with the chunks it returned.
        Chunks can be ``(chunk_id, score)`` tuples, :class:`RetrievedChunk`
        instances, dicts, or bare chunk-id strings. ``source`` labels the
        provenance of this event (see :class:`HardshellClient`); ``None``
        inherits the client default.
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

        Typed spans whose ``source`` was left as ``None`` inherit the
        client's default source; a span-level value (including ``""`` for
        explicitly unlabeled) wins. Raw dict spans are sent verbatim — never
        modified — so the server can apply its own defaults to them.
        """
        serialized = []
        for span in spans:
            if isinstance(span, RetrievalSpan):
                payload = span.to_payload()
                if span.source is None and self._source:
                    payload["source"] = self._source
            else:
                payload = dict(span)
            serialized.append(payload)
        return IngestSpansResult.from_payload(self._post("/v1/spans", {"spans": serialized}))

    # ── Reports (read your own derived data back) ───────────────────────────

    def document_access_report(
        self,
        *,
        window_start: datetime | str | None = None,
        window_end: datetime | str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> DocumentAccessReport:
        """How often your chunks are retrieved, grouped by document
        (``GET /v1/reports/document-access``).

        Args:
            window_start: Only count retrievals at or after this time.
            window_end: Only count retrievals before this time.
            limit: Page size; ``None`` means the server default.
            offset: Page offset for walking large corpora.
        """
        params: dict[str, str] = {}
        if window_start is not None:
            params["window_start"] = _time_param(window_start)
        if window_end is not None:
            params["window_end"] = _time_param(window_end)
        if limit is not None:
            params["limit"] = str(limit)
        if offset is not None:
            params["offset"] = str(offset)
        return DocumentAccessReport.from_payload(self._get("/v1/reports/document-access", params))

    # ── Transport ────────────────────────────────────────────────────────────

    def _post_batch(
        self,
        path: str,
        key: str,
        items: Sequence[Any],
        source: str | None,
    ) -> dict[str, Any]:
        effective = source if source is not None else self._source
        payload: dict[str, Any] = {key: [_payload_of(i) for i in items]}
        if effective:
            payload["source"] = effective
        return self._post(path, payload)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        return self._request("POST", path, body=body)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if params:
            path = path + "?" + urllib.parse.urlencode(params)
        return self._request("GET", path)

    def _request(self, method: str, path: str, body: bytes | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self._base + path,
            data=body,
            method=method,
            headers=self._headers,
        )
        try:
            with _OPENER.open(request, timeout=self._timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise TelemetryError(
                f"hardshell {method} {path} failed: HTTP {exc.code} {detail}",
                status_code=exc.code,
                detail=detail,
                method=method,
                path=path,
            ) from exc
        except urllib.error.URLError as exc:
            raise TelemetryError(
                f"hardshell {method} {path} failed: {exc.reason}",
                method=method,
                path=path,
            ) from exc
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            # urllib only wraps connect-phase failures in URLError; timeouts
            # or resets while reading the response arrive raw.
            raise TelemetryError(
                f"hardshell {method} {path} failed: {exc}",
                method=method,
                path=path,
            ) from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise TelemetryError(
                f"hardshell {method} {path} returned a non-JSON response "
                "(is base_url pointing at your Hardshell endpoint?)",
                status_code=status,
                detail=raw[:200].decode("utf-8", "replace"),
                method=method,
                path=path,
            ) from exc


def _time_param(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return iso_timestamp(value)
    return value
