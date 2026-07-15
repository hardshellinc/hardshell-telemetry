"""Built-in chunkers and the Chunker protocol."""

from __future__ import annotations

import pytest

from hardshell_telemetry import (
    Chunker,
    FixedSizeChunker,
    ParagraphChunker,
    SentenceChunker,
)


class TestProtocol:
    def test_builtins_satisfy_chunker(self):
        for chunker in (FixedSizeChunker(10), ParagraphChunker(), SentenceChunker()):
            assert isinstance(chunker, Chunker)

    def test_duck_typed_custom_chunker_satisfies_protocol(self):
        class Custom:
            def chunk(self, text: str) -> list[str]:
                return [text]

        assert isinstance(Custom(), Chunker)


class TestFixedSizeChunker:
    def test_exact_windows_no_overlap(self):
        assert FixedSizeChunker(4).chunk("abcdefgh") == ["abcd", "efgh"]

    def test_trailing_remainder_kept(self):
        assert FixedSizeChunker(4).chunk("abcdefghij") == ["abcd", "efgh", "ij"]

    def test_overlap_shares_characters(self):
        assert FixedSizeChunker(4, overlap=2).chunk("abcdef") == ["abcd", "cdef"]

    def test_overlap_does_not_duplicate_covered_tail(self):
        # len 6 is fully covered by the second window; no third chunk.
        chunks = FixedSizeChunker(4, overlap=2).chunk("abcdef")
        assert len(chunks) == 2

    def test_text_shorter_than_size_is_one_chunk(self):
        assert FixedSizeChunker(100).chunk("short") == ["short"]

    def test_empty_text_gives_no_chunks(self):
        assert FixedSizeChunker(4).chunk("") == []

    def test_full_coverage_reconstructs_text_without_overlap(self):
        text = "x" * 10 + "y" * 13
        assert "".join(FixedSizeChunker(7).chunk(text)) == text

    def test_invalid_size_rejected(self):
        with pytest.raises(ValueError, match="size"):
            FixedSizeChunker(0)

    def test_overlap_must_be_smaller_than_size(self):
        with pytest.raises(ValueError, match="overlap"):
            FixedSizeChunker(4, overlap=4)


class TestParagraphChunker:
    TEXT = "First para.\n\nSecond para,\nstill second.\n\n\n  Third.  \n"

    def test_splits_on_blank_lines(self):
        assert ParagraphChunker().chunk(self.TEXT) == [
            "First para.",
            "Second para,\nstill second.",
            "Third.",
        ]

    def test_max_chars_packs_consecutive_paragraphs(self):
        chunks = ParagraphChunker(max_chars=45).chunk(self.TEXT)
        assert chunks == ["First para.\n\nSecond para,\nstill second.", "Third."]

    def test_oversized_paragraph_stays_whole(self):
        long_para = "word " * 50
        chunks = ParagraphChunker(max_chars=10).chunk(long_para)
        assert len(chunks) == 1  # never cut inside a paragraph

    def test_empty_and_whitespace_text(self):
        assert ParagraphChunker().chunk("") == []
        assert ParagraphChunker().chunk("  \n\n \n") == []

    def test_invalid_max_chars_rejected(self):
        with pytest.raises(ValueError, match="max_chars"):
            ParagraphChunker(max_chars=0)


class TestSentenceChunker:
    TEXT = "One sentence. Two! Is this three? Yes."

    def test_splits_on_sentence_boundaries(self):
        assert SentenceChunker().chunk(self.TEXT) == [
            "One sentence.",
            "Two!",
            "Is this three?",
            "Yes.",
        ]

    def test_max_chars_packs_sentences(self):
        assert SentenceChunker(max_chars=20).chunk(self.TEXT) == [
            "One sentence. Two!",
            "Is this three? Yes.",
        ]

    def test_no_terminal_punctuation_is_one_chunk(self):
        assert SentenceChunker().chunk("no punctuation here") == ["no punctuation here"]

    def test_empty_text(self):
        assert SentenceChunker().chunk("") == []
