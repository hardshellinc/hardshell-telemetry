"""The intake contract: deterministic id derivation and sensitivity mapping.

The golden values pinned here are a compatibility promise — ids derived by
customers at index-build time must be re-derivable forever. If a change makes
one of these fail, the change is wrong, not the test.
"""

from __future__ import annotations

import uuid

import pytest

from hardshell_telemetry import (
    DEFAULT_SENSITIVITY_SCALE,
    HARDSHELL_DOC_NAMESPACE,
    content_hash,
    corpus_name,
    derive_chunk_id,
    derive_document_id,
    sensitivity_from_level,
)


class TestGoldenValues:
    """Never update these expected values — they pin the wire contract."""

    def test_namespace_is_pinned(self):
        assert HARDSHELL_DOC_NAMESPACE == uuid.UUID("0169b2a1-86c2-5d3a-a28c-45728827aa43")

    def test_namespace_matches_documented_recipe(self):
        assert HARDSHELL_DOC_NAMESPACE == uuid.uuid5(
            uuid.NAMESPACE_URL, "https://hardshell.ai/document"
        )

    def test_document_id_from_key(self):
        assert derive_document_id("agot.csv#row=12") == "543010ef-0d24-562c-b46e-c78b4a21c9e8"

    def test_content_hash(self):
        assert content_hash("hello world") == (
            "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        )

    def test_document_id_from_content(self):
        assert derive_document_id(content="hello world") == ("c9eecd37-7403-5007-b300-4952968bb9c6")


class TestContentHash:
    def test_str_and_utf8_bytes_are_equivalent(self):
        assert content_hash("héllo") == content_hash("héllo".encode())

    def test_raw_bytes_accepted(self):
        assert len(content_hash(b"\x00\x01\x02")) == 64


class TestDeriveDocumentId:
    def test_key_wins_over_content(self):
        with_both = derive_document_id("handbook.pdf", content="v1 text")
        assert with_both == derive_document_id("handbook.pdf")
        assert with_both == derive_document_id("handbook.pdf", content="v2 text")

    def test_content_only_is_deterministic(self):
        assert derive_document_id(content=b"same bytes") == derive_document_id(
            content=b"same bytes"
        )

    def test_str_and_bytes_content_are_equivalent(self):
        assert derive_document_id(content="héllo") == derive_document_id(content="héllo".encode())

    def test_empty_key_falls_back_to_content(self):
        # Guards against row.get("id", "") patterns silently keying everything to "".
        assert derive_document_id("", content="text") == derive_document_id(content="text")

    def test_neither_input_raises_with_fix_in_message(self):
        with pytest.raises(ValueError, match="pass key=.*and/or content="):
            derive_document_id()

    def test_result_is_a_valid_uuid_string(self):
        uuid.UUID(derive_document_id("anything"))


class TestDeriveChunkId:
    def test_format_without_content(self):
        assert derive_chunk_id("doc-uuid", 7) == "doc-uuid:0007"

    def test_format_with_content_appends_hash8(self):
        chunk_id = derive_chunk_id("doc-uuid", 0, content="chunk text")
        assert chunk_id == f"doc-uuid:0000:{content_hash('chunk text')[:8]}"

    def test_deterministic(self):
        assert derive_chunk_id("d", 3, content="x") == derive_chunk_id("d", 3, content="x")

    def test_large_index_widens_naturally(self):
        assert derive_chunk_id("d", 123456) == "d:123456"

    def test_empty_document_id_rejected(self):
        with pytest.raises(ValueError, match="document_id"):
            derive_chunk_id("", 0)

    def test_negative_index_rejected(self):
        with pytest.raises(ValueError, match=">= 0"):
            derive_chunk_id("d", -1)


class TestSensitivityFromLevel:
    def test_default_scale_endpoints_and_spacing(self):
        assert sensitivity_from_level("public") == 0.0
        assert sensitivity_from_level("low") == 0.25
        assert sensitivity_from_level("medium") == 0.5
        assert sensitivity_from_level("high") == 0.75
        assert sensitivity_from_level("critical") == 1.0

    def test_case_and_whitespace_insensitive(self):
        assert sensitivity_from_level("  HIGH ") == 0.75

    def test_custom_scale(self):
        scale = ("internal", "confidential", "secret")
        assert sensitivity_from_level("internal", scale=scale) == 0.0
        assert sensitivity_from_level("confidential", scale=scale) == 0.5
        assert sensitivity_from_level("secret", scale=scale) == 1.0

    def test_unknown_level_names_scale_and_fix(self):
        with pytest.raises(ValueError, match=r"'internal'.*pass scale="):
            sensitivity_from_level("internal")

    def test_single_level_scale_rejected(self):
        with pytest.raises(ValueError, match="at least two"):
            sensitivity_from_level("only", scale=("only",))

    def test_default_scale_is_public_constant(self):
        assert DEFAULT_SENSITIVITY_SCALE == ("public", "low", "medium", "high", "critical")


class TestCorpusName:
    def test_builds_backend_collection(self):
        assert corpus_name("qdrant", "docs-prod") == "qdrant:docs-prod"

    def test_lowercases_and_trims_so_names_cannot_fork(self):
        assert corpus_name(" Qdrant ", " Docs-Prod ") == "qdrant:docs-prod"

    def test_empty_parts_rejected(self):
        with pytest.raises(ValueError, match="non-empty"):
            corpus_name("qdrant", "")
        with pytest.raises(ValueError, match="non-empty"):
            corpus_name("", "docs")

    def test_colon_in_collection_rejected(self):
        with pytest.raises(ValueError, match="may not contain ':'"):
            corpus_name("qdrant", "docs:prod")

    def test_colon_in_backend_rejected(self):
        # A backend with a colon would make "qdrant:prod:docs" ambiguous.
        with pytest.raises(ValueError, match="may not contain ':'"):
            corpus_name("qdrant:prod", "docs")
