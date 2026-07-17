"""Opt-in end-to-end test against a real Hardshell edge.

Skipped unless both env vars are set (never runs in CI):

    HARDSHELL_TEST_BASE_URL=http://127.0.0.1:8088 \
    HARDSHELL_TEST_API_KEY=... \
        uv run pytest tests/test_live_edge.py -v

Exercises the whole id round-trip the library exists to protect: ids
produced by the strategies are registered, retrievals are recorded against
them, and the document-access report must echo the exact same strings back.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from hardshell_telemetry import (
    Chunk,
    DefaultIds,
    Document,
    DocumentLink,
    DocumentRecord,
    HardshellClient,
    content_hash,
    plan_ids,
)

BASE_URL = os.environ.get("HARDSHELL_TEST_BASE_URL")
API_KEY = os.environ.get("HARDSHELL_TEST_API_KEY")

pytestmark = pytest.mark.skipif(
    not (BASE_URL and API_KEY),
    reason="live-edge test: set HARDSHELL_TEST_BASE_URL and HARDSHELL_TEST_API_KEY",
)


def test_id_round_trip_against_live_edge():
    assert BASE_URL and API_KEY  # narrowed by the module-level skipif
    client = HardshellClient(api_key=API_KEY, base_url=BASE_URL, source="testing")
    strategy = DefaultIds()
    run = uuid.uuid4().hex[:8]

    # Document 1: provided id — must pass through verbatim.
    provided_doc_id = f"live-test/{run}/handbook.pdf"
    doc1_id = strategy.document_id(provided_id=provided_doc_id, content="doc one content")
    assert doc1_id == provided_doc_id

    # Document 2: no id — content-derived mint.
    doc2_content = f"doc two content {run}"
    doc2_id = strategy.document_id(content=doc2_content)

    # Chunks: one existing store-style id (verbatim), one minted.
    chunk_existing = f"live-test:{run}:c12:s3"
    chunk1_id = strategy.chunk_id(document_id=doc1_id, index=0, existing_id=chunk_existing)
    assert chunk1_id == chunk_existing
    chunk2_id = strategy.chunk_id(document_id=doc1_id, index=1, content="chunk two text")

    # The dry-run agrees this plan is join-safe before we send anything.
    plan = plan_ids(
        [DocumentRecord(provided_id=provided_doc_id, chunks=[chunk_existing])], strategy
    )
    assert plan.safe, plan.summary()

    # Register both documents and the chunks.
    docs = client.ingest_documents(
        [
            Document(
                document_id=doc1_id,
                name=f"Live round-trip {run}",
                content_hash=content_hash("doc one content"),
            ),
            Document(document_id=doc2_id, content_hash=content_hash(doc2_content)),
        ]
    )
    assert docs.documents_upserted == 2

    chunks = client.ingest_chunks(
        [
            Chunk(chunk_id=chunk1_id, document_links=[DocumentLink(document_id=doc1_id)]),
            Chunk(chunk_id=chunk2_id, document_links=[DocumentLink(document_id=doc1_id)]),
        ]
    )
    assert chunks.chunks_upserted == 2

    # Record retrievals referencing exactly those ids.
    spans = client.record_retrieval(
        chunks=[(chunk1_id, 0.91), (chunk2_id, 0.88)],
        user_id=f"live-test-{run}",
        backend="live-test",
    )
    assert spans.spans_accepted == 1

    # The report must echo the ids back byte-for-byte.
    deadline = time.monotonic() + 30
    matched = None
    while time.monotonic() < deadline:
        report = client.document_access_report()
        matched = next((d for d in report.documents if d.document_id == doc1_id), None)
        if matched and {c.chunk_id for c in matched.chunks} >= {chunk1_id, chunk2_id}:
            break
        time.sleep(2)

    assert matched is not None, f"document {doc1_id!r} never appeared in the report"
    reported_ids = {c.chunk_id for c in matched.chunks}
    assert chunk1_id in reported_ids, "verbatim chunk id did not round-trip"
    assert chunk2_id in reported_ids, "minted chunk id did not round-trip"
    accessed = {c.chunk_id: c.access_count for c in matched.chunks}
    assert accessed[chunk1_id] >= 1
    assert accessed[chunk2_id] >= 1

    # The content-derived document exists too (registered, zero retrievals).
    report = client.document_access_report()
    assert any(d.document_id == doc2_id for d in report.documents), (
        "content-derived document id did not round-trip"
    )
