"""Identity strategies and the migration dry-run."""

from __future__ import annotations

import uuid

import pytest

from hardshell_telemetry import (
    ChunkRecord,
    DefaultIds,
    DerivedIds,
    DocumentRecord,
    ExistingIds,
    IdStrategy,
    LegacyNamespaceIds,
    content_hash,
    derive_chunk_id,
    derive_document_id,
    plan_ids,
)


class TestProtocol:
    def test_builtins_satisfy_strategy(self):
        for strategy in (
            DefaultIds(),
            ExistingIds(),
            DerivedIds(),
            LegacyNamespaceIds(uuid.uuid4()),
        ):
            assert isinstance(strategy, IdStrategy)


class TestDefaultIds:
    def test_provided_document_id_passes_through_verbatim(self):
        assert DefaultIds().document_id(provided_id="kb/handbook.pdf") == "kb/handbook.pdf"

    def test_provided_id_wins_even_with_content(self):
        assert DefaultIds().document_id(provided_id="kb-1", content="text") == "kb-1"

    def test_no_id_mints_content_derived(self):
        minted = DefaultIds().document_id(content="hello world")
        assert minted == derive_document_id(content="hello world")

    def test_nothing_at_all_errors_with_fix(self):
        with pytest.raises(ValueError, match="pass your system's id"):
            DefaultIds().document_id()

    def test_existing_chunk_id_passes_through_verbatim(self):
        chunk_id = DefaultIds().chunk_id(document_id="d", index=0, existing_id="agot:c12:s3")
        assert chunk_id == "agot:c12:s3"

    def test_missing_chunk_id_mints(self):
        assert DefaultIds().chunk_id(document_id="d", index=7) == derive_chunk_id("d", 7)


class TestExistingIds:
    def test_verbatim(self):
        assert ExistingIds().document_id(provided_id="pk-42") == "pk-42"
        assert ExistingIds().chunk_id(document_id="pk-42", index=0, existing_id="c-1") == "c-1"

    def test_missing_document_id_is_loud(self):
        with pytest.raises(ValueError, match="requires a document id"):
            ExistingIds().document_id(content="content is not enough")

    def test_missing_chunk_id_is_loud_and_names_the_chunk(self):
        with pytest.raises(ValueError, match=r"chunk 3 of document 'pk-42'"):
            ExistingIds().chunk_id(document_id="pk-42", index=3)


class TestDerivedIds:
    def test_document_id_always_minted_under_hardshell_namespace(self):
        assert DerivedIds().document_id(provided_id="agot.csv#row=12") == (
            "543010ef-0d24-562c-b46e-c78b4a21c9e8"  # golden value from test_intake
        )

    def test_chunk_format_options(self):
        strategy = DerivedIds(separator="/", index_width=2, content_hash_chars=4)
        chunk_id = strategy.chunk_id(document_id="d", index=3, content="text")
        assert chunk_id == f"d/03/{content_hash('text')[:4]}"

    def test_default_chunk_format_matches_pinned_intake_function(self):
        assert DerivedIds().chunk_id(document_id="d", index=7) == derive_chunk_id("d", 7)


class TestLegacyNamespaceIds:
    NS = uuid.UUID("12345678-1234-5678-1234-567812345678")

    def test_reproduces_customer_namespace_scheme(self):
        strategy = LegacyNamespaceIds(self.NS)
        assert strategy.document_id(provided_id="doc-1") == str(uuid.uuid5(self.NS, "doc-1"))
        assert strategy.chunk_id(document_id="x", index=0, existing_id="c-1") == str(
            uuid.uuid5(self.NS, "c-1")
        )

    def test_accepts_namespace_as_string(self):
        assert LegacyNamespaceIds(str(self.NS)).document_id(provided_id="doc-1") == str(
            uuid.uuid5(self.NS, "doc-1")
        )

    def test_missing_ids_are_loud(self):
        with pytest.raises(ValueError, match="needs a document id"):
            LegacyNamespaceIds(self.NS).document_id()
        with pytest.raises(ValueError, match="requires a chunk id"):
            LegacyNamespaceIds(self.NS).chunk_id(document_id="d", index=0)


class TestPlanIds:
    def test_all_verbatim_corpus_is_safe(self):
        records = [
            DocumentRecord(provided_id="doc-1", chunks=["c-1", "c-2"]),
            DocumentRecord(provided_id="doc-2", chunks=[ChunkRecord(existing_id="c-3")]),
        ]
        plan = plan_ids(records, DefaultIds())
        assert plan.safe
        assert plan.documents_total == 2
        assert plan.chunks_total == 3
        assert plan.document_changes_total == 0
        assert "SAFE" in plan.summary()

    def test_derived_strategy_reports_id_changes_with_samples(self):
        records = [DocumentRecord(provided_id="doc-1", chunks=["c-1"])]
        plan = plan_ids(records, DerivedIds())
        assert not plan.safe
        assert plan.document_changes_total == 1
        assert plan.chunk_changes_total == 1
        current, planned = plan.document_change_samples[0]
        assert current == "doc-1"
        assert planned == derive_document_id("doc-1")
        assert "IDS WILL CHANGE" in plan.summary()

    def test_missing_ids_become_errors_not_exceptions(self):
        records = [DocumentRecord(name="No Id Here", chunks=["c-1"])]
        plan = plan_ids(records, ExistingIds())
        assert not plan.safe
        assert len(plan.errors) == 1
        assert "No Id Here" in plan.errors[0]

    def test_content_only_identity_warns_fork_on_edit(self):
        plan = plan_ids([DocumentRecord(content="just text")], DefaultIds())
        assert plan.minted_documents_total == 1
        assert any("forks this document" in w for w in plan.warnings)

    def test_document_collision_warns(self):
        records = [
            DocumentRecord(content="same text", name="a"),
            DocumentRecord(content="same text", name="b"),
        ]
        plan = plan_ids(records, DefaultIds())
        assert any("silently merge" in w for w in plan.warnings)

    def test_chunk_collision_warns(self):
        records = [
            DocumentRecord(provided_id="doc-1", chunks=["c-1"]),
            DocumentRecord(provided_id="doc-2", chunks=["c-1"]),
        ]
        plan = plan_ids(records, DefaultIds())
        assert any("indistinguishable" in w for w in plan.warnings)

    def test_sample_cap_respected_but_totals_full(self):
        records = [DocumentRecord(provided_id=f"doc-{i}") for i in range(25)]
        plan = plan_ids(records, DerivedIds(), sample_limit=5)
        assert plan.document_changes_total == 25
        assert len(plan.document_change_samples) == 5

    def test_erroring_document_still_counts_its_chunks(self):
        plan = plan_ids([DocumentRecord(chunks=["c-1", "c-2"])], ExistingIds())
        assert plan.chunks_total == 2
        assert len(plan.errors) == 1

    def test_deterministic(self):
        records = [DocumentRecord(provided_id="d", content="x", chunks=["c"])]
        assert plan_ids(records, DefaultIds()) == plan_ids(records, DefaultIds())
