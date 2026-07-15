"""End-to-end client behavior against the fake edge."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hardshell_telemetry import (
    Chunk,
    Document,
    DocumentLink,
    HardshellClient,
    RetrievalSpan,
    RetrievedChunk,
    TelemetryError,
)


class TestConstruction:
    def test_api_key_required(self):
        with pytest.raises(ValueError, match="api_key"):
            HardshellClient(api_key="", base_url="http://example.invalid")

    def test_base_url_required(self):
        with pytest.raises(ValueError, match="base_url"):
            HardshellClient(api_key="k", base_url="")

    def test_trailing_slash_normalized(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url + "/")
        client.ingest_documents([])
        assert edge.last.path == "/v1/documents"


class TestHeaders:
    def test_bearer_auth_and_content_type(self, edge, client):
        client.ingest_documents([])
        assert edge.last.headers["Authorization"] == "Bearer test-key"
        assert edge.last.headers["Content-Type"] == "application/json"

    def test_user_agent_carries_package_version(self, edge, client):
        from hardshell_telemetry import __version__

        client.ingest_documents([])
        assert edge.last.headers["User-Agent"] == f"hardshell-telemetry/{__version__}"


class TestIngestDocuments:
    def test_typed_documents_and_result(self, edge, client):
        edge.respond("POST", "/v1/documents", body={"documents_upserted": 2})
        result = client.ingest_documents(
            [
                Document(document_id="doc-1", name="handbook", sensitivity=0.2),
                Document(document_id="doc-2"),
            ]
        )
        assert result.documents_upserted == 2
        assert edge.last.json == {
            "documents": [
                {"document_id": "doc-1", "name": "handbook", "sensitivity": 0.2},
                {"document_id": "doc-2"},
            ]
        }

    def test_dicts_pass_through_verbatim(self, edge, client):
        client.ingest_documents([{"document_id": "doc-1", "anything": {"goes": True}}])
        assert edge.last.json["documents"] == [{"document_id": "doc-1", "anything": {"goes": True}}]

    def test_call_source_overrides_client_source(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url, source="production")
        client.ingest_documents([], source="backfill")
        assert edge.last.json["source"] == "backfill"

    def test_client_source_is_default(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url, source="production")
        client.ingest_documents([])
        assert edge.last.json["source"] == "production"

    def test_empty_string_source_forces_unlabeled(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url, source="production")
        client.ingest_documents([], source="")
        assert "source" not in edge.last.json

    def test_unlabeled_client_omits_source(self, edge, client):
        client.ingest_documents([])
        assert "source" not in edge.last.json


class TestIngestChunks:
    def test_chunks_with_links_and_result(self, edge, client):
        edge.respond("POST", "/v1/chunks", body={"chunks_upserted": 1, "links_upserted": 1})
        result = client.ingest_chunks(
            [Chunk(chunk_id="c-1", document_links=[DocumentLink(document_id="doc-1")])]
        )
        assert (result.chunks_upserted, result.links_upserted) == (1, 1)
        assert edge.last.path == "/v1/chunks"
        assert edge.last.json["chunks"] == [
            {"chunk_id": "c-1", "document_links": [{"document_id": "doc-1"}]}
        ]


class TestSpans:
    def test_record_retrieval_common_case(self, edge, client):
        edge.respond("POST", "/v1/spans", body={"spans_accepted": 1, "chunks_logged": 2})
        result = client.record_retrieval(
            chunks=[("c-1", 0.91), RetrievedChunk("c-2", 0.88)],
            user_id="end-user-123",
            backend="chroma",
        )
        assert result.spans_accepted == 1
        (span,) = edge.last.json["spans"]
        assert span["backend"] == "chroma"
        assert span["user_id"] == "end-user-123"
        assert span["chunks"] == [
            {"chunk_id": "c-1", "score": 0.91},
            {"chunk_id": "c-2", "score": 0.88},
        ]
        # timestamp filled in automatically, timezone-aware ISO 8601
        assert datetime.fromisoformat(span["timestamp"]).tzinfo is not None

    def test_record_retrieval_carries_actor_and_correlation_fields(self, edge, client):
        ts = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
        client.record_retrieval(
            chunks=["c-1"],
            backend="qdrant",
            user_id="u-1",
            session_id="s-1",
            ip="10.0.0.9",
            trace_id="t-1",
            span_id="sp-1",
            timestamp=ts,
            attributes={"route": "/ask"},
            source="staging",
        )
        (span,) = edge.last.json["spans"]
        assert span["timestamp"] == "2026-07-13T12:00:00+00:00"
        assert span["session_id"] == "s-1"
        assert span["ip"] == "10.0.0.9"
        assert span["trace_id"] == "t-1"
        assert span["span_id"] == "sp-1"
        assert span["attributes"] == {"route": "/ask"}
        assert span["source"] == "staging"

    def test_ingest_spans_batches_multiple(self, edge, client):
        client.ingest_spans([RetrievalSpan(chunks=["c-1"]), RetrievalSpan(chunks=["c-2"])])
        assert len(edge.last.json["spans"]) == 2

    def test_typed_span_with_unset_source_inherits_client_source(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url, source="production")
        client.ingest_spans([RetrievalSpan(chunks=["c-1"])])
        assert edge.last.json["spans"][0]["source"] == "production"

    def test_empty_string_span_source_forces_unlabeled(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url, source="production")
        client.record_retrieval(chunks=["c-1"], source="")
        assert "source" not in edge.last.json["spans"][0]

    def test_raw_dict_spans_sent_verbatim_never_modified(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url, source="production")
        raw_span = {"backend": "pgvector", "timestamp": "2026-07-13T00:00:00+00:00"}
        client.ingest_spans([raw_span])
        assert edge.last.json["spans"] == [
            {"backend": "pgvector", "timestamp": "2026-07-13T00:00:00+00:00"}
        ]
        assert "source" not in raw_span  # caller's dict untouched too

    def test_string_chunks_container_rejected(self, client):
        with pytest.raises(TypeError, match="wrap it in a list"):
            client.record_retrieval(chunks="doc-42:chunk-3")

    def test_span_level_source_wins_over_client_source(self, edge):
        client = HardshellClient(api_key="k", base_url=edge.base_url, source="production")
        client.ingest_spans([RetrievalSpan(source="evaluation")])
        assert edge.last.json["spans"][0]["source"] == "evaluation"


class TestDocumentAccessReport:
    def test_no_args_sends_no_query_params(self, edge, client):
        client.document_access_report()
        assert edge.last.method == "GET"
        assert edge.last.path == "/v1/reports/document-access"
        assert edge.last.query == {}

    def test_limit_zero_is_sent_not_dropped(self, edge, client):
        client.document_access_report(limit=0)
        assert edge.last.query == {"limit": ["0"]}

    def test_window_and_paging_params(self, edge, client):
        client.document_access_report(
            window_start=datetime(2026, 7, 1, tzinfo=UTC),
            window_end="2026-07-13T00:00:00+00:00",
            limit=50,
            offset=100,
        )
        assert edge.last.query == {
            "window_start": ["2026-07-01T00:00:00+00:00"],
            "window_end": ["2026-07-13T00:00:00+00:00"],
            "limit": ["50"],
            "offset": ["100"],
        }

    def test_response_parsed_into_types(self, edge, client):
        edge.respond(
            "GET",
            "/v1/reports/document-access",
            body={
                "documents": [
                    {
                        "document_id": "doc-1",
                        "name": "handbook",
                        "chunk_count": 1,
                        "chunks": [{"chunk_id": "c-1", "access_count": 3}],
                    }
                ],
                "total_documents": 1,
            },
        )
        report = client.document_access_report()
        assert report.total_documents == 1
        assert report.documents[0].chunks[0].access_count == 3


class TestErrors:
    def test_http_error_carries_status_and_detail(self, edge, client):
        edge.respond("POST", "/v1/spans", status=401, body={"detail": "invalid api key"})
        with pytest.raises(TelemetryError) as excinfo:
            client.record_retrieval(chunks=["c-1"])
        assert excinfo.value.status_code == 401
        assert "invalid api key" in (excinfo.value.detail or "")
        assert "401" in str(excinfo.value)

    def test_validation_error_surfaces_as_telemetry_error(self, edge, client):
        edge.respond(
            "POST", "/v1/documents", status=422, body={"detail": [{"msg": "field required"}]}
        )
        with pytest.raises(TelemetryError) as excinfo:
            client.ingest_documents([{"wrong": "shape"}])
        assert excinfo.value.status_code == 422

    def test_connection_failure_raises_telemetry_error(self):
        client = HardshellClient(api_key="k", base_url="http://127.0.0.1:9", timeout=0.5)
        with pytest.raises(TelemetryError) as excinfo:
            client.ingest_documents([])
        assert excinfo.value.status_code is None

    def test_read_timeout_raises_telemetry_error(self, edge):
        # Server accepts the connection but stalls past the client timeout;
        # urllib surfaces this as a raw TimeoutError, not URLError.
        edge.respond("POST", "/v1/documents", delay=1.0)
        client = HardshellClient(api_key="k", base_url=edge.base_url, timeout=0.2)
        with pytest.raises(TelemetryError):
            client.ingest_documents([])

    def test_non_json_success_body_raises_telemetry_error(self, edge, client):
        edge.respond(
            "POST",
            "/v1/documents",
            raw_body=b"<html>corporate proxy sign-in</html>",
            content_type="text/html",
        )
        with pytest.raises(TelemetryError) as excinfo:
            client.ingest_documents([])
        assert excinfo.value.status_code == 200
        assert "corporate proxy" in (excinfo.value.detail or "")

    def test_redirects_refused_not_followed(self, make_edge, client, edge):
        # Following a redirect would replay the POST as a body-less GET and
        # re-send the bearer token to the redirect target.
        other = make_edge()
        edge.respond(
            "POST", "/v1/spans", status=302, headers={"Location": other.base_url + "/elsewhere"}
        )
        with pytest.raises(TelemetryError) as excinfo:
            client.record_retrieval(chunks=[("c-1", 0.5)])
        assert excinfo.value.status_code == 302
        assert other.requests == []  # nothing leaked to the redirect target

    def test_error_carries_method_and_path(self, edge, client):
        edge.respond("GET", "/v1/reports/document-access", status=500, body={"detail": "boom"})
        with pytest.raises(TelemetryError) as excinfo:
            client.document_access_report()
        assert excinfo.value.method == "GET"
        assert excinfo.value.path == "/v1/reports/document-access"


class TestFakeEdgeIsolation:
    def test_two_edges_record_independently(self, make_edge):
        first, second = make_edge(), make_edge()
        HardshellClient(api_key="k", base_url=first.base_url).ingest_documents([])
        HardshellClient(api_key="k", base_url=second.base_url).ingest_chunks([])
        assert [r.path for r in first.requests] == ["/v1/documents"]
        assert [r.path for r in second.requests] == ["/v1/chunks"]
