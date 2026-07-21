"""Serialization contracts for the typed payloads."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from hardshell_telemetry import (
    Chunk,
    ChunkAccessCount,
    CorpusAccessCount,
    Document,
    DocumentAccessReport,
    DocumentLink,
    IngestChunksResult,
    IngestDocumentsResult,
    IngestSpansResult,
    RetrievalSpan,
    RetrievedChunk,
)
from hardshell_telemetry.types import iso_timestamp, retrieved_chunk_payload


class TestDocument:
    def test_minimal_payload_has_only_document_id(self):
        assert Document(document_id="doc-1").to_payload() == {"document_id": "doc-1"}

    def test_full_payload_round_trips_every_field(self):
        payload = Document(
            document_id="doc-1",
            name="Q3 board deck",
            content_hash="ab" * 32,
            sensitivity=0.9,
            sensitivity_level="confidential",
            custom_metadata={"owner": "finance"},
            simhash_hex="deadbeef",
        ).to_payload()
        assert payload == {
            "document_id": "doc-1",
            "name": "Q3 board deck",
            "content_hash": "ab" * 32,
            "sensitivity": 0.9,
            "sensitivity_level": "confidential",
            "custom_metadata": {"owner": "finance"},
            "simhash_hex": "deadbeef",
        }

    def test_none_fields_are_omitted_not_nulled(self):
        payload = Document(document_id="doc-1", sensitivity=0.5).to_payload()
        assert "name" not in payload
        assert "custom_metadata" not in payload


class TestChunk:
    def test_minimal_payload_has_only_chunk_id(self):
        assert Chunk(chunk_id="c-1").to_payload() == {"chunk_id": "c-1"}

    def test_document_links_serialize_typed_and_dict_forms(self):
        payload = Chunk(
            chunk_id="c-1",
            document_links=[
                DocumentLink(document_id="doc-1", link_metadata={"coverage": 0.6}),
                {"document_id": "doc-2"},
            ],
        ).to_payload()
        assert payload["document_links"] == [
            {"document_id": "doc-1", "link_metadata": {"coverage": 0.6}},
            {"document_id": "doc-2"},
        ]

    def test_link_metadata_omitted_when_none(self):
        assert DocumentLink(document_id="doc-1").to_payload() == {"document_id": "doc-1"}

    def test_classification_fields(self):
        payload = Chunk(
            chunk_id="c-1",
            sensitivity=0.7,
            sensitivity_level="high",
            pii_flags={"email": True},
            taxonomy={"dept": "legal"},
        ).to_payload()
        assert payload["pii_flags"] == {"email": True}
        assert payload["taxonomy"] == {"dept": "legal"}


class TestRetrievedChunks:
    def test_dataclass_form(self):
        assert RetrievedChunk("c-1", 0.91).to_payload() == {"chunk_id": "c-1", "score": 0.91}

    def test_tuple_form(self):
        assert retrieved_chunk_payload(("c-1", 0.5)) == {"chunk_id": "c-1", "score": 0.5}

    def test_bare_string_form_omits_score(self):
        assert retrieved_chunk_payload("c-1") == {"chunk_id": "c-1"}

    def test_dict_form_passes_through_verbatim(self):
        chunk = {"chunk_id": "c-1", "score": None, "rank": 3}
        assert retrieved_chunk_payload(chunk) == {"chunk_id": "c-1", "score": None, "rank": 3}

    def test_dict_form_is_copied_not_aliased(self):
        chunk = {"chunk_id": "c-1"}
        payload = retrieved_chunk_payload(chunk)
        payload["mutated"] = True
        assert "mutated" not in chunk

    def test_score_coerced_to_float(self):
        assert retrieved_chunk_payload(("c-1", 1)) == {"chunk_id": "c-1", "score": 1.0}


class TestRetrievalSpan:
    def test_explicit_timestamp_serialized_iso(self):
        ts = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
        payload = RetrievalSpan(chunks=["c-1"], backend="chroma", timestamp=ts).to_payload()
        assert payload["timestamp"] == "2026-07-13T12:00:00+00:00"
        assert payload["backend"] == "chroma"
        assert payload["chunks"] == [{"chunk_id": "c-1"}]

    def test_missing_timestamp_defaults_to_now_utc(self):
        payload = RetrievalSpan().to_payload()
        parsed = datetime.fromisoformat(payload["timestamp"])
        assert parsed.tzinfo is not None
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 5

    def test_naive_timestamp_assumed_utc(self):
        assert iso_timestamp(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05+00:00"

    def test_all_actor_fields_carried(self):
        payload = RetrievalSpan(
            user_id="u-1",
            session_id="s-1",
            ip="10.0.0.1",
            trace_id="t-1",
            span_id="sp-1",
            attributes={"route": "/search"},
            source="staging",
        ).to_payload()
        assert payload["user_id"] == "u-1"
        assert payload["session_id"] == "s-1"
        assert payload["ip"] == "10.0.0.1"
        assert payload["trace_id"] == "t-1"
        assert payload["span_id"] == "sp-1"
        assert payload["attributes"] == {"route": "/search"}
        assert payload["source"] == "staging"

    def test_empty_optional_fields_omitted_from_wire(self):
        payload = RetrievalSpan(chunks=["c-1"], backend="chroma").to_payload()
        assert set(payload) == {"backend", "timestamp", "chunks"}

    def test_corpus_carried_when_set_omitted_when_none(self):
        with_corpus = RetrievalSpan(chunks=["c-1"], backend="qdrant", corpus="qdrant:docs")
        assert with_corpus.to_payload()["corpus"] == "qdrant:docs"
        assert "corpus" not in RetrievalSpan(chunks=["c-1"], backend="qdrant").to_payload()

    def test_timestamp_captured_at_construction_not_send_time(self):
        span = RetrievalSpan(chunks=["c-1"])
        first = span.to_payload()["timestamp"]
        time.sleep(0.02)
        assert span.to_payload()["timestamp"] == first

    def test_string_chunks_container_rejected(self):
        with pytest.raises(TypeError, match="wrap it in a list"):
            RetrievalSpan(chunks="doc-42:chunk-3")


class TestResultParsing:
    def test_ingest_results(self):
        assert IngestDocumentsResult.from_payload({"documents_upserted": 3}).documents_upserted == 3
        chunks = IngestChunksResult.from_payload({"chunks_upserted": 10, "links_upserted": 4})
        assert (chunks.chunks_upserted, chunks.links_upserted) == (10, 4)
        spans = IngestSpansResult.from_payload({"spans_accepted": 1, "chunks_logged": 5})
        assert (spans.spans_accepted, spans.chunks_logged) == (1, 5)

    def test_ingest_results_tolerate_empty_payload(self):
        assert IngestSpansResult.from_payload({}).spans_accepted == 0

    def test_document_access_report(self):
        report = DocumentAccessReport.from_payload(
            {
                "documents": [
                    {
                        "document_id": "doc-1",
                        "name": "handbook",
                        "chunk_count": 2,
                        "chunks": [
                            {"chunk_id": "c-1", "access_count": 7},
                            {"chunk_id": "c-2", "access_count": 0},
                        ],
                    }
                ],
                "total_documents": 40,
            }
        )
        assert report.total_documents == 40
        assert report.documents[0].name == "handbook"
        assert report.documents[0].chunks == (
            ChunkAccessCount("c-1", 7),
            ChunkAccessCount("c-2", 0),
        )
        assert report.documents[0].corpora == ()  # absent in payload → empty

    def test_document_access_report_parses_corpora_breakdown(self):
        report = DocumentAccessReport.from_payload(
            {
                "documents": [
                    {
                        "document_id": "doc-1",
                        "chunk_count": 1,
                        "chunks": [{"chunk_id": "c-1", "access_count": 3}],
                        "corpora": [
                            {"corpus": "", "access_count": 1},
                            {"corpus": "qdrant:docs-prod", "access_count": 2},
                        ],
                    }
                ],
                "total_documents": 1,
            }
        )
        assert report.documents[0].corpora == (
            CorpusAccessCount("", 1),
            CorpusAccessCount("qdrant:docs-prod", 2),
        )
