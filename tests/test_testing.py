"""The public test double and data factories in hardshell_telemetry.testing."""

from __future__ import annotations

from hardshell_telemetry import (
    Chunk,
    ChunkAccessCount,
    CorpusAccessCount,
    Document,
    DocumentLink,
    RetrievalSpan,
    TelemetryClient,
)
from hardshell_telemetry.testing import (
    MockTelemetryClient,
    make_chunk,
    make_document,
    make_report,
    make_span,
    make_summary,
)


class TestMockTelemetryClient:
    def test_is_a_telemetry_client(self):
        assert isinstance(MockTelemetryClient(), TelemetryClient)

    def test_records_documents_with_source_and_corpus(self):
        mock = MockTelemetryClient()
        docs = [Document(document_id="d-1"), Document(document_id="d-2")]
        result = mock.ingest_documents(docs, corpus="qdrant:docs", source="index-build")
        assert result.documents_upserted == 2
        (call,) = mock.ingest_documents_calls
        assert (call.corpus, call.source) == ("qdrant:docs", "index-build")
        assert mock.documents == docs

    def test_chunk_links_are_counted(self):
        mock = MockTelemetryClient()
        result = mock.ingest_chunks([make_chunk("c-1"), make_chunk("c-2", document_id=None)])
        assert result.chunks_upserted == 2
        assert result.links_upserted == 1  # only the first chunk is linked

    def test_record_retrieval_is_visible_as_a_span(self):
        mock = MockTelemetryClient()
        result = mock.record_retrieval(
            chunks=[("c-1", 0.9), ("c-2", 0.8)],
            user_id="u-1",
            backend="qdrant",
            corpus="qdrant:docs",
        )
        assert (result.spans_accepted, result.chunks_logged) == (1, 2)
        (span,) = mock.spans
        assert isinstance(span, RetrievalSpan)
        assert (span.user_id, span.corpus) == ("u-1", "qdrant:docs")

    def test_spans_flatten_across_batches(self):
        mock = MockTelemetryClient()
        mock.ingest_spans([make_span(["c-1"]), make_span(["c-2"])])
        mock.record_retrieval(chunks=["c-3"])
        assert len(mock.spans) == 3
        assert len(mock.ingest_spans_calls) == 2  # one batch of two, one from record_retrieval

    def test_report_defaults_to_empty_and_records_the_query(self):
        mock = MockTelemetryClient()
        report = mock.document_access_report(limit=20)
        assert report.documents == ()
        assert report.total_documents == 0
        assert mock.report_queries[0].limit == 20

    def test_seeded_report_is_returned(self):
        seeded = make_report(
            [make_summary("handbook", corpora=[CorpusAccessCount("qdrant:docs", 3)])]
        )
        mock = MockTelemetryClient(report=seeded)
        report = mock.document_access_report()
        assert report.total_documents == 1
        assert report.documents[0].corpora[0] == CorpusAccessCount("qdrant:docs", 3)


class TestFactories:
    def test_make_document_defaults_and_overrides(self):
        assert make_document().document_id == "doc-1"
        assert make_document("d-9", name="Nine").name == "Nine"

    def test_make_chunk_auto_links_to_document(self):
        chunk = make_chunk("doc-1:0")
        assert isinstance(chunk, Chunk)
        assert chunk.document_links == [DocumentLink(document_id="doc-1")]

    def test_make_chunk_unlinked(self):
        assert make_chunk("c-1", document_id=None).document_links == ()

    def test_make_span_builds_scored_chunks(self):
        span = make_span(["c-1", "c-2"], score=0.5, user_id="u-1")
        payload = span.to_payload()
        assert [c["chunk_id"] for c in payload["chunks"]] == ["c-1", "c-2"]
        assert payload["chunks"][0]["score"] == 0.5
        assert payload["user_id"] == "u-1"

    def test_make_summary_infers_chunk_count(self):
        summary = make_summary(
            "d-1", chunks=[ChunkAccessCount("c-1", 4), ChunkAccessCount("c-2", 0)]
        )
        assert summary.chunk_count == 2

    def test_make_report_infers_total(self):
        report = make_report([make_summary("d-1"), make_summary("d-2")])
        assert report.total_documents == 2
